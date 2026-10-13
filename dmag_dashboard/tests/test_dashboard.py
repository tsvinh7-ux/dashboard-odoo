from datetime import timedelta

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestDmagDashboard(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.groups_id |= cls.env.ref('dmag_dashboard.group_dmag_dashboard_manager')
        cls.now = fields.Datetime.now()
        cls.customer = cls.env['res.partner'].create({'name': 'KH Test', 'is_company': True})
        cls.learner = cls.env['res.partner'].create({'name': 'HV Test', 'parent_id': cls.customer.id})
        cls.project = cls.env['project.project'].create({'name': 'Triển khai Test', 'partner_id': cls.customer.id})
        Task = cls.env['project.task']
        cls.late_open = Task.create({'name': 'Trễ', 'project_id': cls.project.id,
                                     'date_deadline': cls.now - timedelta(days=3)})
        cls.done_ontime = Task.create({'name': 'Xong đúng hạn', 'project_id': cls.project.id,
                                       'date_deadline': cls.now + timedelta(days=3)})
        cls.done_ontime.state = '1_done'
        cls.channel = cls.env['slide.channel'].create({'name': 'Khóa Test'})
        cls.slide = cls.env['slide.slide'].create({'name': 'Bài 1', 'channel_id': cls.channel.id,
                                                   'slide_category': 'article', 'is_published': True})
        cls.env['slide.slide'].create({'name': 'Bài 2', 'channel_id': cls.channel.id,
                                       'slide_category': 'article', 'is_published': True})
        cls.channel._action_add_members(cls.learner)

    def _kpis(self, data):
        return {k['key']: k for k in data['kpis']}

    def test_closed_date_tracks_state(self):
        self.assertTrue(self.done_ontime.dmag_closed_date)
        self.done_ontime.state = '01_in_progress'
        self.assertFalse(self.done_ontime.dmag_closed_date)

    def test_metrics_and_health(self):
        data = self.env['dmag.dashboard'].get_dashboard_data({'period': 30})
        kpis = self._kpis(data)
        self.assertEqual(kpis['overdue']['value'], 1)
        self.assertEqual(kpis['ontime_rate']['value'], 100.0)
        self.assertGreaterEqual(kpis['enrollments']['value'], 1)
        row = next(c for c in data['customers'] if c['id'] == self.customer.id)
        self.assertEqual(row['overdue'], 1)
        self.assertLess(row['score'], 100)
        self.assertEqual(row['tasks_total'], 2)

    def test_stalled_learner(self):
        enrollment = self.env['slide.channel.partner'].search([('partner_id', '=', self.learner.id)])
        self.env.flush_all()
        self.env.cr.execute("UPDATE slide_channel_partner SET create_date = %s WHERE id = %s",
                            (self.now - timedelta(days=40), enrollment.id))
        self.env.invalidate_all()
        data = self.env['dmag.dashboard'].get_dashboard_data({'period': 30})
        self.assertEqual(self._kpis(data)['stalled']['value'], 1)
        # one completed lesson today -> not stalled any more
        self.env['slide.slide.partner'].create({'slide_id': self.slide.id, 'partner_id': self.learner.id,
                                                'completed': True})
        data = self.env['dmag.dashboard'].get_dashboard_data({'period': 30})
        self.assertEqual(self._kpis(data)['stalled']['value'], 0)

    def test_drilldown_matches_kpi(self):
        Dash = self.env['dmag.dashboard']
        action = Dash.action_drill({'type': 'kpi', 'key': 'overdue'}, {'period': 30})
        self.assertEqual(self.env[action['res_model']].search(action['domain']), self.late_open)
        detail = Dash.get_customer_detail(self.customer.id, {'period': 30})
        self.assertEqual([t['id'] for t in detail['tasks']], [self.late_open.id])
        self.assertEqual(len(detail['learners']), 1)

    def test_alert_rule_triggers(self):
        rule = self.env['dmag.alert.rule'].create({
            'name': 'Quá hạn tăng', 'metric': 'overdue', 'direction': 'up', 'threshold': 20,
            'min_volume': 1, 'min_zscore': 0})
        alerts = self.env['dmag.alert']._run_checks(rule.ids)
        self.assertEqual(len(alerts), 1)
        self.assertIn(alerts.severity, ('warning', 'critical'))
        # cooldown: a second run does not spam
        self.assertFalse(self.env['dmag.alert']._run_checks(rule.ids))
