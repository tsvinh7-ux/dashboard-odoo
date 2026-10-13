import statistics
from collections import defaultdict
from datetime import timedelta

from markupsafe import Markup, escape

from odoo import api, fields, models, _



def _fmt(value, unit):
    if value is None:
        return '—'
    if unit == 'percent':
        return f"{value:.1f}%".replace('.', ',')
    return f"{value:,.0f}".replace(',', '.') if abs(value) >= 10 else f"{value:.1f}".rstrip('0').rstrip('.').replace('.', ',')


def _fmt_money(value):
    a = abs(value)
    if a >= 1e9:
        return f"{value / 1e9:.2f} tỷ".replace('.', ',')
    if a >= 1e6:
        return f"{value / 1e6:.1f} tr".replace('.', ',')
    return _fmt(value, 'count')


class DmagAlertRule(models.Model):
    _name = 'dmag.alert.rule'
    _description = 'Quy tắc cảnh báo trồi sụt'
    _order = 'sequence, id'

    name = fields.Char('Tên quy tắc', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company)
    metric = fields.Selection(lambda self: self.env['dmag.board']._all_metric_selection(),
                              string='Chỉ số', required=True)
    board = fields.Char(compute='_compute_board', store=True, index=True)
    window_days = fields.Integer('Cửa sổ (ngày)', default=7, required=True,
                                 help="Giá trị hiện tại = chỉ số trong N ngày gần nhất.")
    baseline_periods = fields.Integer('Số kỳ nền', default=4, required=True,
                                      help="So với trung bình của K cửa sổ liền trước.")
    direction = fields.Selection([('both', 'Tăng hoặc giảm'), ('up', 'Chỉ khi tăng'), ('down', 'Chỉ khi giảm')],
                                 string='Chiều biến động', default='both', required=True)
    threshold = fields.Float('Ngưỡng (%)', default=25.0, required=True,
                             help="Mức thay đổi so với nền để kích hoạt. Với chỉ số tỷ lệ: điểm phần trăm.")
    min_zscore = fields.Float('Độ bất thường tối thiểu (z)', default=1.0,
                              help="Chỉ cảnh báo khi lệch khỏi dao động thông thường ít nhất z độ lệch chuẩn. 0 = tắt.")
    min_volume = fields.Float('Khối lượng tối thiểu', default=3.0,
                              help="Bỏ qua khi cả giá trị hiện tại và nền đều nhỏ hơn số này (tránh báo động giả).")
    cooldown_hours = fields.Integer('Không lặp lại trong (giờ)', default=24)
    user_ids = fields.Many2many('res.users', string='Người nhận',
                                help="Để trống: gửi cho nhóm Quản trị Dashboard DMAG.")
    alert_ids = fields.One2many('dmag.alert', 'rule_id')
    last_triggered = fields.Datetime(compute='_compute_last_triggered')

    @api.depends('metric')
    def _compute_board(self):
        for rule in self:
            rule.board = rule.metric.split('.')[0] if rule.metric else False

    @api.depends('alert_ids.date')
    def _compute_last_triggered(self):
        for rule in self:
            rule.last_triggered = max(rule.alert_ids.mapped('date'), default=False)

    def _evaluate(self, engine, D):
        """GA-Insights style check: last window vs mean of the K previous windows."""
        self.ensure_one()
        key = self.metric.split('.', 1)[1]
        meta = engine._dmag_metrics().get(key)
        if not meta:
            return None
        now = D['sc']['now']
        w = timedelta(days=self.window_days)
        cur = engine._metric(D, key, now - w, now)
        base = [engine._metric(D, key, now - w * (i + 1), now - w * i)
                for i in range(1, self.baseline_periods + 1)]
        base = [b for b in base if b is not None]
        if cur is None or not base:
            return None
        mean = statistics.mean(base)
        sd = statistics.pstdev(base) if len(base) > 1 else 0.0
        if meta['unit'] == 'percent':
            change = cur - mean
        else:
            if max(cur, mean) < self.min_volume:
                return None
            change = 100.0 * (cur - mean) / mean if mean else 100.0
        if abs(change) < self.threshold or cur == mean:
            return None
        move = 'up' if cur > mean else 'down'
        if self.direction != 'both' and move != self.direction:
            return None
        z = (cur - mean) / sd if sd else None
        if self.min_zscore and z is not None and abs(z) < self.min_zscore:
            return None
        if not meta['good']:
            severity = 'info'
        elif move == meta['good']:
            severity = 'positive'
        else:
            severity = 'critical' if abs(change) >= 2 * self.threshold else 'warning'
        unit = '%' if meta['unit'] != 'percent' else ' điểm %'
        if meta['unit'] == 'money':
            fmt_cur, fmt_base = _fmt_money(cur), _fmt_money(mean)
        elif meta['unit'] == 'days':
            fmt_cur, fmt_base = _('%s ngày', _fmt(cur, 'count')), _('%s ngày', _fmt(mean, 'count'))
        else:
            fmt_cur, fmt_base = _fmt(cur, meta['unit']), _fmt(mean, meta['unit'])
        window = _('%s ngày qua', self.window_days) if self.window_days != 7 else _('7 ngày qua')
        if not mean:
            message = _('%(label)s %(window)s: %(cur)s — cả %(k)s kỳ trước đều bằng 0.',
                        label=meta['label'], window=window, cur=fmt_cur, k=len(base))
        else:
            message = _('%(label)s %(window)s %(verb)s %(chg)s%(unit)s so với trung bình %(k)s kỳ trước (%(cur)s so với %(base)s).',
                        label=meta['label'], window=window, verb=_('tăng') if move == 'up' else _('giảm'),
                        chg=_fmt(abs(change), 'count'), unit=unit, k=len(base),
                        cur=fmt_cur, base=fmt_base)
        return {
            'rule_id': self.id, 'title': self.name, 'metric': key, 'severity': severity,
            'drill': {'type': 'kpi', 'key': key, 'board': engine._dmag_key},
            'value': cur, 'baseline': round(mean, 2), 'change': round(change, 1), 'zscore': z and round(z, 2),
            'direction': move, 'message': message, 'window_days': self.window_days,
        }

    def action_run_now(self):
        self.env['dmag.alert']._run_checks(self.ids)
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('Đã kiểm tra'), 'message': _('Xem kết quả trong Nhật ký cảnh báo.'),
            'type': 'success', 'next': {'type': 'ir.actions.act_window_close'}}}


class DmagAlert(models.Model):
    _name = 'dmag.alert'
    _description = 'Cảnh báo điều hành'
    _order = 'date desc, id desc'

    rule_id = fields.Many2one('dmag.alert.rule', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='rule_id.company_id', store=True)
    name = fields.Char(related='rule_id.name', store=True, string='Cảnh báo')
    metric = fields.Selection(lambda self: self.env['dmag.board']._all_metric_selection(), string='Chỉ số')
    date = fields.Datetime('Thời điểm', default=fields.Datetime.now, required=True)
    value = fields.Float('Giá trị')
    baseline = fields.Float('Nền')
    change = fields.Float('Thay đổi')
    severity = fields.Selection([('critical', 'Nghiêm trọng'), ('warning', 'Cần chú ý'),
                                 ('positive', 'Tín hiệu tốt'), ('info', 'Thông tin')], string='Mức độ')
    message = fields.Text('Nội dung')
    state = fields.Selection([('new', 'Mới'), ('ack', 'Đã xử lý')], default='new', string='Trạng thái')

    def action_ack(self):
        self.write({'state': 'ack'})

    @api.model
    def _run_checks(self, rule_ids=None):
        Rule = self.env['dmag.alert.rule'].sudo()
        rules = Rule.browse(rule_ids) if rule_ids else Rule.search([])
        now = fields.Datetime.now()
        created = self.browse()
        groups = defaultdict(Rule.browse)
        for rule in rules:
            groups[(rule.company_id or self.env.company, rule.board)] |= rule
        for (company, board_key), crules in groups.items():
            board = self.env['dmag.board'].sudo()._board(board_key).with_company(company).with_context(
                allowed_company_ids=[company.id])
            if not board._available():
                continue
            sc = board._scope({'period': 7})
            since = min(now - timedelta(days=r.window_days * (r.baseline_periods + 1)) for r in crules)
            D = board._load(sc, since)
            for rule in crules.with_company(company):
                res = rule._evaluate(board, D)
                if not res:
                    continue
                if rule.alert_ids.filtered(lambda a: a.date >= now - timedelta(hours=rule.cooldown_hours)):
                    continue
                alert = self.sudo().create({
                    'rule_id': rule.id, 'metric': rule.metric, 'value': res['value'] or 0.0,
                    'baseline': res['baseline'], 'change': res['change'],
                    'severity': res['severity'], 'message': res['message'],
                })
                alert._notify(rule)
                created |= alert
        return created

    def _notify(self, rule):
        self.ensure_one()
        users = rule.user_ids or self.env.ref('dmag_dashboard.group_dmag_dashboard_manager').users
        partners = users.partner_id
        icon = {'critical': '🔴', 'warning': '🟠', 'positive': '🟢', 'info': '🔵'}[self.severity]
        channel = self.env.ref('dmag_dashboard.channel_dmag_alerts', raise_if_not_found=False)
        if channel:
            missing = partners - channel.channel_member_ids.partner_id
            if missing:
                channel.sudo().add_members(partner_ids=missing.ids, post_joined_message=False)
            channel.sudo().message_post(
                body=Markup('<p><b>%s %s</b><br/>%s</p>') % (icon, escape(rule.name), escape(self.message)),
                message_type='comment', subtype_xmlid='mail.mt_comment',
                author_id=self.env.ref('base.partner_root').id)
        toast = {'critical': 'danger', 'warning': 'warning', 'positive': 'success', 'info': 'info'}[self.severity]
        for partner in partners:
            self.env['bus.bus']._sendone(partner, 'simple_notification', {
                'title': f"{icon} {rule.name}", 'message': self.message,
                'type': toast, 'sticky': self.severity == 'critical',
            })

    @api.model
    def _cron_run_checks(self):
        self._run_checks()
