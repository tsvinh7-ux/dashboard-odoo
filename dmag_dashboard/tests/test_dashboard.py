from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError
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

    def _hotspots(self, data):
        return next(p for row in data['rows'] for p in row['panels'] if p['id'] == 'hotspots')['rows']

    def test_closed_date_tracks_state(self):
        self.assertTrue(self.done_ontime.dmag_closed_date)
        self.done_ontime.state = '01_in_progress'
        self.assertFalse(self.done_ontime.dmag_closed_date)

    def test_metrics_and_health(self):
        data = self.env['dmag.board.learning'].get_board({'period': 30})
        kpis = self._kpis(data)
        self.assertEqual(kpis['overdue']['value'], 1)
        self.assertEqual(kpis['ontime_rate']['value'], 100.0)
        self.assertGreaterEqual(kpis['enrollments']['value'], 1)
        row = next(r for r in self._hotspots(data) if r['id'] == self.customer.id)
        self.assertEqual(row['cells']['overdue']['v'], 1)
        self.assertLess(row['cells']['score']['v'], 100)
        self.assertEqual(row['cells']['deploy']['sub'], '1/2 đầu việc')

    def test_stalled_learner(self):
        enrollment = self.env['slide.channel.partner'].search([('partner_id', '=', self.learner.id)])
        self.env.flush_all()
        self.env.cr.execute("UPDATE slide_channel_partner SET create_date = %s WHERE id = %s",
                            (self.now - timedelta(days=40), enrollment.id))
        self.env.invalidate_all()
        data = self.env['dmag.board.learning'].get_board({'period': 30})
        self.assertEqual(self._kpis(data)['stalled']['value'], 1)
        # one completed lesson today -> not stalled any more
        self.env['slide.slide.partner'].create({'slide_id': self.slide.id, 'partner_id': self.learner.id,
                                                'completed': True})
        data = self.env['dmag.board.learning'].get_board({'period': 30})
        self.assertEqual(self._kpis(data)['stalled']['value'], 0)

    def test_drilldown_matches_kpi(self):
        Dash = self.env['dmag.board.learning']
        action = Dash.action_drill({'type': 'kpi', 'key': 'overdue'}, {'period': 30})
        self.assertEqual(self.env[action['res_model']].search(action['domain']), self.late_open)
        detail = Dash.get_detail('customer', self.customer.id, {'period': 30})
        sections = {s['title']: s['items'] for s in detail['sections']}
        self.assertEqual([t['drill']['id'] for t in sections['Đầu việc đang mở']], [self.late_open.id])
        self.assertEqual(len(sections['Học viên']), 1)

    def test_alert_rule_triggers(self):
        rule = self.env['dmag.alert.rule'].create({
            'name': 'Quá hạn tăng', 'metric': 'learning.overdue', 'direction': 'up', 'threshold': 20,
            'min_volume': 1, 'min_zscore': 0})
        alerts = self.env['dmag.alert']._run_checks(rule.ids)
        self.assertEqual(len(alerts), 1)
        self.assertIn(alerts.severity, ('warning', 'critical'))
        # cooldown: a second run does not spam
        self.assertFalse(self.env['dmag.alert']._run_checks(rule.ids))


@tagged('post_install', '-at_install')
class TestDmagBoards(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.groups_id |= cls.env.ref('dmag_dashboard.group_dmag_dashboard_manager')

    def test_every_board_renders(self):
        """Each board returns a coherent payload (KPIs, rows, drill-downs) whatever data exists."""
        for board in self.env['dmag.board']._boards():
            data = board.get_board({'period': 30})
            self.assertEqual(data['board']['key'], board._dmag_key)
            for kpi in data['kpis']:
                action = board.action_drill({'type': 'kpi', 'key': kpi['key'], 'board': kpi['board']}, {'period': 30})
                self.assertEqual(action['type'], 'ir.actions.act_window', f"{board._name} {kpi['key']}")
            for row in data['rows']:
                self.assertEqual(len(row['widths']), len(row['panels']))

    def test_sales_metrics(self):
        if 'crm.lead' not in self.env:
            self.skipTest('crm not installed')
        Lead = self.env['crm.lead']
        won_stage = self.env['crm.stage'].search([('is_won', '=', True)], limit=1)
        won = Lead.create({'name': 'Thắng', 'type': 'opportunity', 'expected_revenue': 1000})
        won.stage_id = won_stage
        lost = Lead.create({'name': 'Thua', 'type': 'opportunity', 'expected_revenue': 500})
        lost.action_set_lost()
        Lead.create({'name': 'Mở', 'type': 'opportunity', 'expected_revenue': 300})
        kpis = {k['key']: k for k in self.env['dmag.board.sales'].get_board({'period': 30})['kpis']}
        self.assertEqual(kpis['win_rate']['value'], 50.0)
        self.assertEqual(kpis['pipeline']['value'], 300)
        self.assertEqual(kpis['opps_new']['value'], 3)

    def test_finance_aging_and_overdue(self):
        partner = self.env['res.partner'].create({'name': 'KH nợ'})
        today = fields.Date.today()
        inv = self.env['account.move'].create({
            'move_type': 'out_invoice', 'partner_id': partner.id,
            'invoice_date': today - timedelta(days=40), 'invoice_date_due': today - timedelta(days=10),
            'invoice_line_ids': [(0, 0, {'name': 'Dịch vụ', 'quantity': 1, 'price_unit': 1000, 'tax_ids': []})],
        })
        inv.action_post()
        board = self.env['dmag.board.finance']
        kpis = {k['key']: k for k in board.get_board({'period': 30})['kpis']}
        self.assertAlmostEqual(kpis['ar_overdue']['value'], 1000)
        detail = board.get_detail('customer', partner.id, {'period': 30})
        self.assertEqual(detail['sections'][0]['items'][0]['drill']['id'], inv.id)

    def test_role_access(self):
        user = self.env['res.users'].create({
            'name': 'NV KD', 'login': 'nvkd_dmag_test',
            'groups_id': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('dmag_dashboard.group_dmag_role_sales').id])]})
        sales = self.env['dmag.board.sales'].with_user(user)
        self.assertEqual([b['key'] for b in sales.get_menu()], ['sales'])
        sales.get_board({'period': 30})
        with self.assertRaises(AccessError):
            self.env['dmag.board.finance'].with_user(user).get_board({'period': 30})
        with self.assertRaises(AccessError):
            self.env['dmag.board.executive'].with_user(user).get_board({'period': 30})

    def test_executive_aggregates_boards(self):
        data = self.env['dmag.board.executive'].get_board({'period': 30})
        cards = data['rows'][0]['panels'][0]['items']
        self.assertTrue({'learning', 'finance'} <= {c['key'] for c in cards})
        self.assertTrue(all(k['board'] != 'executive' for k in data['kpis']))
