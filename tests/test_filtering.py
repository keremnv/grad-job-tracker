import unittest
from jobwatch.feeds import Posting
from jobwatch.filtering import classify, rank, role_family


def p(title='Graduate Software Engineer', location='London, United Kingdom', country='', countries=(), description=''):
    return Posting('Example','ashby','example','id',title,location,'https://jobs.ashbyhq.com/example/id',country=country,countries=countries,description=description)


class ScreeningTests(unittest.TestCase):
    def test_role_families_and_non_graduate_titles(self):
        for title,family in [('Junior Python Engineer','backend'),('Associate Java Developer','backend'),('Graduate Compiler Engineer','platform_systems'),('Junior MLOps Engineer','ml_ai'),('Research Software Engineer','ml_ai'),('Developer Tools Engineer','platform_systems'),('Software Engineer','software')]:
            with self.subTest(title=title):
                self.assertEqual(role_family(title),family)
                self.assertNotEqual(classify(p(title))[0],'excluded')
        self.assertEqual(classify(p('Junior Python Engineer'))[0],'priority')
        self.assertEqual(classify(p('Software Engineer'))[0],'review')

    def test_false_positive_placements_seniority_and_sales(self):
        for title in ('Product Engineer Placement (6 Months)','Product Engineer Placement (1 year)','Software Engineering Intern','Senior Platform Engineer','Staff ML Engineer','Software Sales Engineer','AI Product Manager','Senior Software Engineer Graduate Mentor'):
            with self.subTest(title=title): self.assertEqual(classify(p(title))[0],'excluded')

    def test_titles_from_boards_verified_17_october_2026(self):
        # Riverlane's 2027 scheme was previously excluded as role family "other" and so never tracked.
        self.assertEqual(role_family('Engineering Graduate Scheme 2027'),'engineering_programme')
        self.assertEqual(classify(p('Engineering Graduate Scheme 2027','Cambridge, UK, Cambridge'))[0],'priority')
        self.assertEqual(role_family('Graduate Software Engineer (2027 start)'),'software')
        for title in ('Graduate Mechanical Engineer (2027 start)','Business & Operations Graduate 2027','Drug Delivery Business Developer','People Operations Associate (AI and Automation)','Technology Leader - Wireless Software Systems'):
            with self.subTest(title=title): self.assertEqual(classify(p(title,'United Kingdom'))[0],'excluded')
        self.assertEqual(classify(p('Software/Infrastructure Graduate - 2027 - Hong Kong','Hong Kong'))[0],'excluded')
        self.assertEqual(classify(p('Software/Infrastructure Graduate - 2027 - London','London, London - Sloane Street'))[0],'review')

    def test_unknown_city_and_remote_are_reviewed(self):
        for location in ('London','Cambridge','Remote','EMEA','Worldwide','', 'Dublin'):
            with self.subTest(location=location):
                judgment,reason=classify(p(location=location));self.assertEqual(judgment,'review');self.assertIn('unknown',reason)
        self.assertEqual(classify(p(location='Remote',country='GB'))[0],'priority')

    def test_multi_location_and_conflicting_primary_country(self):
        self.assertEqual(classify(p(location='New York; London',country='US',countries=('US','GB')))[0],'priority')
        self.assertEqual(classify(p(location='New York; London',country='US'))[0],'review')
        self.assertEqual(classify(p(location='Cambridge, Massachusetts',country='US'))[0],'excluded')
        self.assertEqual(classify(p(location='London, Ontario',country='CA'))[0],'excluded')
        self.assertEqual(classify(p(location='New York',country='US'))[0],'excluded')
        self.assertEqual(classify(p(location='Berlin',country='DE'))[0],'excluded')

    def test_year_and_experience_uncertainties_not_invented_eligibility(self):
        for title in ('2026 Graduate Software Engineer','Graduate Software Engineer 2028','Graduate Developer (2025/2027)','2027/2028 Graduate AI Engineer'):
            judgment,reason=classify(p(title));self.assertEqual(judgment,'review');self.assertIn('flexibility unknown',reason)
        self.assertEqual(classify(p('2027 Graduate Software Engineer'))[0],'priority')
        for desc in ('Requires 3+ years professional experience','PhD required','Strong experience in backend services'):
            self.assertEqual(classify(p(description=desc))[0],'review')
        self.assertIn('owner eligibility unknown',classify(p(description='Sole British National; DV clearance'))[1])

    def test_no_calendar_cutoff_and_no_prestige_ranking(self):
        self.assertEqual(classify(p(description='Start July 2027'))[0],'priority')
        self.assertEqual(classify(p(description='Applications open February 2027'))[0],'priority')
        rows=[dict(priority='review',title='Backend Engineer',company='A',external_id='1'),dict(priority='review',title='Junior Backend Engineer 2028',company='Z',external_id='2'),dict(priority='priority',title='Graduate Software Engineer',company='Z',external_id='3')]
        self.assertEqual([r['external_id'] for r in sorted(rows,key=rank)],['3','2','1'])
