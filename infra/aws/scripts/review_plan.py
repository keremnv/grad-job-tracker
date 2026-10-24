#!/usr/bin/env python3
"""Before-bucket read-only plan in an isolated LOCAL backend. NEVER apply this plan.

The operational root keeps its required S3 backend. No existing state/cache, data,
credentials or private configuration is copied into the review tree.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from check_plan import check


def prepare(source, output):
    source = source.resolve()
    output = output.resolve()
    if output.is_relative_to(source.parents[1]):
        raise ValueError('Review directory must be outside the source checkout')
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    module = output/'source/infra/aws'
    module.mkdir(parents=True)
    paths = list(source.glob('*.tf'))+list(source.glob('*.asc'))+[source/'.terraform.lock.hcl']
    paths += list((source/'scripts').glob('*.py'))+list((source/'scripts').glob('*.sh'))
    paths += list((source/'systemd').glob('*.service'))+list((source/'systemd').glob('*.timer'))
    for path in paths:
        if path.is_symlink() or path.name.endswith('_override.tf'):
            raise ValueError('Unexpected symlink or backend override in reviewed source')
        target = module/path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path,target)
    sql = module.parents[1]/'sql/postgresql/bootstrap.sql'
    sql.parent.mkdir(parents=True)
    shutil.copyfile(source.parents[1]/'sql/postgresql/bootstrap.sql',sql)
    # Official override semantics replace the entire backend type/configuration.
    (module/'review_override.tf').write_text('terraform {\n backend "local" {\n path = '+
        json.dumps(str(output/'REVIEW_ONLY.tfstate'))+'\n }\n}\n')
    return module


def review(source, output, variables, *, run_command=subprocess.run):
    module = prepare(source,output)
    env = {k:v for k,v in os.environ.items() if not k.startswith(('TF_CLI_ARGS','TF_LOG')) and k!='TF_WORKSPACE'}
    env.update(TF_DATA_DIR=str(output.resolve()/'cache'),TF_IN_AUTOMATION='1',TF_INPUT='0')
    init_args = ['init','-input=false','-no-color','-lockfile=readonly']
    plugins = source.resolve()/'.terraform/providers'
    if plugins.is_dir():
        init_args.append('-plugin-dir='+str(plugins))
    for phase, args in (
        ('init',init_args),
        ('plan',['plan','-input=false','-no-color','-var-file='+str(variables.resolve()),
                 '-out='+str(output.resolve()/'REVIEW_ONLY.tfplan')])):
        log = output/(phase+'.log')
        with log.open('x') as stream:
            os.chmod(log,0o600)
            result = run_command(['terraform','-chdir='+str(module),*args],env=env,
                                 stdout=stream,stderr=subprocess.STDOUT,timeout=600)
        if result.returncode:
            (output/'REVIEW_ONLY.tfplan').unlink(missing_ok=True)
            raise ValueError('Review '+phase+' failed; inspect its private log. No resources applied.')
    try:
        result = run_command(['terraform','-chdir='+str(module),'show','-json',str(output.resolve()/'REVIEW_ONLY.tfplan')],
                             env=env,capture_output=True,text=True,timeout=60)
    finally:
        # Retain review JSON/logs, not a local-backend binary someone could apply later.
        (output/'REVIEW_ONLY.tfplan').unlink(missing_ok=True)
    if result.returncode:
        raise ValueError('Could not inspect private review plan')
    plan = json.loads(result.stdout)
    summary = check(plan)
    summary['review_only'] = True
    for name,data in (('REVIEW_ONLY.plan.json',plan),('summary.json',summary)):
        fd = os.open(output/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as stream:
            json.dump(data,stream,indent=2,sort_keys=True);stream.write('\n')
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--vars',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args = p.parse_args()
    try:
        result = review(Path(__file__).resolve().parents[1],args.output,args.vars)
        print(json.dumps(result,indent=2,sort_keys=True))
        print('LOCAL-BACKEND REVIEW ONLY. Approval must reference a new S3-backed plan.')
        return 0
    except (ValueError,OSError,KeyError,subprocess.SubprocessError):
        print('Pre-deployment review failed; inspect private logs. No apply operation exists here.',file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
