from collections import defaultdict
from datetime import timedelta

from odoo import api, fields, models, _

from .board_base import col, dt, to_dt

STALE_DAYS = 14


class DmagBoardSales(models.AbstractModel):
    _name = 'dmag.board.sales'
    _inherit = 'dmag.board'
    _description = 'DMAG Board · Kinh doanh'

    _dmag_key = 'sales'
    _dmag_title = 'Kinh doanh'
    _dmag_icon = 'fa-line-chart'
    _dmag_sequence = 20
    _dmag_group = 'dmag_dashboard.group_dmag_role_sales'
    _dmag_requires = ('crm.lead', 'sale.order')
    _dmag_action = 'dmag_dashboard.action_dmag_board_sales'

    @api.model
    def _dmag_metrics(self):
        return {
            'sales_amount': {'label': 'Doanh số đơn bán', 'tag': 'Bán', 'accent': 'navy', 'unit': 'money',
                             'good': 'up', 'hint': 'Giá trị trước thuế các đơn bán được xác nhận trong kỳ.'},
            'opps_new': {'label': 'Cơ hội mới', 'tag': 'Pipeline', 'unit': 'count', 'good': 'up',
                         'hint': 'Cơ hội (opportunity) được tạo trong kỳ.'},
            'win_rate': {'label': 'Tỷ lệ chốt', 'tag': 'Pipeline', 'kind': 'ratio', 'unit': 'percent',
                         'good': 'up', 'hint': 'Cơ hội thắng / (thắng + thua) trong các cơ hội đóng trong kỳ.'},
            'pipeline': {'label': 'Pipeline đang mở', 'tag': 'Pipeline', 'kind': 'state', 'unit': 'money',
                         'good': 'up', 'hint': 'Tổng doanh thu dự kiến của các cơ hội đang mở (tại cuối kỳ).'},
            'cycle_days': {'label': 'Chu kỳ chốt TB', 'tag': 'Hiệu suất', 'kind': 'ratio', 'unit': 'days',
                           'good': 'down', 'hint': 'Số ngày trung bình từ lúc tạo tới lúc thắng của cơ hội thắng trong kỳ.'},
            'stale': {'label': 'Cơ hội nguội', 'tag': 'Hiệu suất', 'kind': 'state', 'unit': 'count', 'good': 'down',
                      'hint': f'Cơ hội đang mở đứng yên ở một giai đoạn quá {STALE_DAYS} ngày (tại cuối kỳ).'},
            'won_value': {'label': 'Giá trị cơ hội thắng', 'unit': 'money', 'good': 'up',
                          'hint': 'Tổng doanh thu dự kiến của cơ hội thắng trong kỳ.'},
            'opps_won': {'label': 'Cơ hội thắng', 'unit': 'count', 'good': 'up', 'hint': 'Số cơ hội thắng trong kỳ.'},
            'opps_lost': {'label': 'Cơ hội thua', 'unit': 'count', 'good': 'down', 'hint': 'Số cơ hội thua trong kỳ.'},
        }

    @api.model
    def _kpi_order(self):
        return ['sales_amount', 'opps_new', 'win_rate', 'pipeline', 'cycle_days', 'stale']

    def _filter(self, sc):
        users = self.env['res.users'].search([('share', '=', False)], limit=300)
        return {'label': _('Toàn đội kinh doanh'), 'options': self._users_options(users)}

    def _lead_domain(self, sc):
        dom = [('type', '=', 'opportunity'), ('company_id', 'in', sc['company_ids'] + [False])]
        if sc['filter_id']:
            dom.append(('user_id', '=', sc['filter_id']))
        return dom

    def _so_domain(self, sc):
        dom = [('company_id', 'in', sc['company_ids'])]
        if sc['filter_id']:
            dom.append(('user_id', '=', sc['filter_id']))
        return dom

    def _load(self, sc, since):
        D = super()._load(sc, since)
        Lead = self.env['crm.lead'].sudo().with_context(active_test=False)
        leads = Lead.search(self._lead_domain(sc) + [
            '|', '|', ('create_date', '>=', since), ('date_closed', '>=', since),
            '&', ('date_closed', '=', False), ('active', '=', True)])
        D['leads'] = []
        for lead in leads:
            won = lead.active and (lead.stage_id.is_won or lead.probability >= 100)
            closed = dt(lead.date_closed) or (dt(lead.write_date) if not lead.active else None)
            D['leads'].append({
                'id': lead.id, 'name': lead.name, 'partner': lead.partner_id.display_name or lead.partner_name or '',
                'create': dt(lead.create_date), 'closed': closed if (won or not lead.active) else None,
                'won': won, 'lost': not lead.active, 'value': lead.expected_revenue,
                'stage': lead.stage_id.id, 'user': lead.user_id.id,
                'last_stage': dt(lead.date_last_stage_update) or dt(lead.create_date),
                'next_activity': lead.activity_date_deadline, 'deadline': lead.date_deadline,
                'lost_reason': lead.lost_reason_id.id,
            })
        orders = self.env['sale.order'].sudo().search(self._so_domain(sc) + [
            ('state', '=', 'sale'), ('date_order', '>=', since)])
        D['orders'] = [{'id': o.id, 'date': dt(o.date_order), 'user': o.user_id.id,
                        'customer': o.partner_id.commercial_partner_id.id,
                        'amount': self._money(o.amount_untaxed, o.currency_id, o.date_order.date(), sc)}
                       for o in orders]
        quotes = self.env['sale.order'].sudo().search(self._so_domain(sc) + [('state', 'in', ('draft', 'sent'))])
        D['quotes'] = [{'id': q.id, 'create': dt(q.create_date), 'validity': q.validity_date,
                        'amount': self._money(q.amount_untaxed, q.currency_id, None, sc)} for q in quotes]
        return D

    # ------------------------------------------------------------------ metrics
    @staticmethod
    def _open_at(lead, t):
        if lead['create'] > t:
            return False
        if lead['closed']:
            return t < lead['closed']
        return not lead['won'] and not lead['lost']

    def _stale_at(self, lead, t, now):
        if not self._open_at(lead, t) or lead['last_stage'] > t:
            return False
        if t - lead['last_stage'] <= timedelta(days=STALE_DAYS):
            return False
        if t >= now - timedelta(seconds=2) and lead['next_activity'] and to_dt(lead['next_activity']) >= now:
            return False  # a planned activity today means somebody is on it
        return True

    def _metric(self, D, key, start, end):
        leads = D['leads']
        if key == 'sales_amount':
            return sum(o['amount'] for o in D['orders'] if start <= o['date'] < end)
        if key == 'opps_new':
            return sum(1 for x in leads if start <= x['create'] < end)
        if key in ('opps_won', 'won_value', 'cycle_days'):
            won = [x for x in leads if x['won'] and x['closed'] and start <= x['closed'] < end]
            if key == 'opps_won':
                return len(won)
            if key == 'won_value':
                return sum(x['value'] for x in won)
            return round(sum((x['closed'] - x['create']).days for x in won) / len(won), 1) if won else None
        if key == 'opps_lost':
            return sum(1 for x in leads if x['lost'] and x['closed'] and start <= x['closed'] < end)
        if key == 'win_rate':
            closed = [x for x in leads if x['closed'] and start <= x['closed'] < end]
            return round(100.0 * sum(1 for x in closed if x['won']) / len(closed), 1) if closed else None
        if key == 'pipeline':
            return sum(x['value'] for x in leads if self._open_at(x, end))
        if key == 'stale':
            return sum(1 for x in leads if self._stale_at(x, end, D['sc']['now']))
        raise ValueError(key)

    def _kpi_sub(self, D, key):
        sc = D['sc']
        start, now = sc['start'], sc['now']
        if key == 'sales_amount':
            return _('%s đơn bán', sum(1 for o in D['orders'] if start <= o['date'] < now))
        if key == 'win_rate':
            closed = [x for x in D['leads'] if x['closed'] and start <= x['closed'] < now]
            return _('%s thắng / %s đóng', sum(1 for x in closed if x['won']), len(closed))
        if key == 'pipeline':
            return _('%s cơ hội đang mở', sum(1 for x in D['leads'] if self._open_at(x, now)))
        if key == 'opps_new':
            return {'money': self._metric(D, 'won_value', start, now), 'prefix': _('Thắng ')}
        if key == 'stale':
            value = sum(x['value'] for x in D['leads'] if self._stale_at(x, now, now))
            return {'money': value, 'prefix': _('Giá trị bị treo ')}
        return ''

    # ------------------------------------------------------------------ panels
    def _open_leads(self, D):
        now = D['sc']['now']
        return [x for x in D['leads'] if self._open_at(x, now)]

    def _rows(self, D):
        sc = D['sc']
        now, today = sc['now'], sc['today']
        open_leads = self._open_leads(D)
        stages = self.env['crm.stage'].sudo().search([('is_won', '=', False)], order='sequence, id')
        by_stage = defaultdict(list)
        for x in open_leads:
            by_stage[x['stage']].append(x)
        funnel_stages = [{
            'key': s.id, 'label': s.name, 'count': len(by_stage[s.id]),
            'value': sum(x['value'] for x in by_stage[s.id]),
            'drill': {'type': 'stage', 'id': s.id, 'title': s.name}} for s in stages]
        old_quotes = [q for q in D['quotes'] if now - q['create'] > timedelta(days=14)]
        past_deadline = [x['id'] for x in open_leads if x['deadline'] and x['deadline'] < today]
        funnel = {
            'type': 'funnel', 'id': 'pipeline', 'title': _('Pipeline theo giai đoạn'),
            'subtitle': _('Cơ hội đang mở và giá trị dự kiến ở từng bước'), 'value_unit': 'money',
            'stages': funnel_stages, 'empty': '' if open_leads else _('Không có cơ hội đang mở.'),
            'tiles': [
                {'label': _('Báo giá đang chờ'), 'value': sum(q['amount'] for q in D['quotes']), 'unit': 'money',
                 'hint': _('%s báo giá chưa chốt', len(D['quotes'])),
                 'drill': {'type': 'ids', 'model': 'sale.order', 'ids': [q['id'] for q in D['quotes']],
                           'title': _('Báo giá đang chờ')}},
                {'label': _('Báo giá > 14 ngày'), 'value': len(old_quotes), 'unit': 'count',
                 'alert': bool(old_quotes),
                 'drill': {'type': 'ids', 'model': 'sale.order', 'ids': [q['id'] for q in old_quotes],
                           'title': _('Báo giá treo quá 14 ngày')}},
                {'label': _('Quá ngày dự kiến chốt'), 'value': len(past_deadline), 'unit': 'count',
                 'alert': bool(past_deadline),
                 'drill': {'type': 'ids', 'model': 'crm.lead', 'ids': past_deadline,
                           'title': _('Cơ hội quá ngày dự kiến chốt')}},
            ],
        }
        trend = self._trend(D, [(_('Doanh số'), ['sales_amount', 'won_value']),
                                (_('Cơ hội'), ['opps_new', 'opps_won', 'opps_lost', 'pipeline'])])
        return [
            {'widths': [5, 7], 'panels': [funnel, trend]},
            {'widths': [7, 5], 'panels': [self._leaderboard(D), self._lost_reasons(D)]},
            {'widths': [12], 'panels': [self._hot_deals(D)]},
        ]

    def _leaderboard(self, D):
        sc = D['sc']
        start, now, today = sc['start'], sc['now'], sc['today']
        stats = defaultdict(lambda: {'sales': 0.0, 'won': 0, 'closed': 0, 'pipeline': 0.0, 'stale': 0, 'late': 0})
        for o in D['orders']:
            if start <= o['date'] < now:
                stats[o['user']]['sales'] += o['amount']
        for x in D['leads']:
            s = stats[x['user']]
            if x['closed'] and start <= x['closed'] < now:
                s['closed'] += 1
                s['won'] += x['won']
            if self._open_at(x, now):
                s['pipeline'] += x['value']
                s['stale'] += self._stale_at(x, now, now)
                s['late'] += bool(x['next_activity'] and x['next_activity'] < today)
        names = {u.id: u.name for u in self.env['res.users'].sudo().browse([u for u in stats if u])}
        rows = []
        for uid, s in stats.items():
            rate = round(100.0 * s['won'] / s['closed']) if s['closed'] else None
            rows.append({
                'id': uid, 'open': {'type': 'user', 'id': uid}, '_sort': -s['sales'] - s['pipeline'] / 1000,
                'cells': {
                    'name': {'v': names.get(uid) or _('Chưa phân công'), 'avatar': True, 'tone': None if uid else 'bad'},
                    'sales': {'v': s['sales']}, 'won': s['won'],
                    'rate': {'v': rate, 'tone': None if rate is None else 'good' if rate >= 40 else 'warn'
                             if rate >= 20 else 'bad'},
                    'pipeline': {'v': s['pipeline']},
                    'stale': {'v': s['stale'] or None, 'tone': 'bad' if s['stale'] else None},
                    'late': {'v': s['late'] or None, 'tone': 'warn' if s['late'] else None},
                }})
        rows.sort(key=lambda r: r.pop('_sort'))
        return {'type': 'table', 'id': 'leaderboard', 'title': _('Đội kinh doanh'),
                'subtitle': _('Doanh số kỳ này, tỷ lệ chốt & sức khỏe pipeline · bấm để mở cơ hội của từng người'),
                'columns': [col('name', _('Nhân viên')), col('sales', _('Doanh số'), 'money'),
                            col('won', _('Thắng'), 'num'), col('rate', _('Tỷ lệ chốt'), 'pct'),
                            col('pipeline', _('Pipeline'), 'money'), col('stale', _('Nguội'), 'num'),
                            col('late', _('Việc trễ'), 'num')],
                'rows': rows, 'empty': _('Chưa có dữ liệu kinh doanh.')}

    def _lost_reasons(self, D):
        sc = D['sc']
        counts = defaultdict(lambda: [0, 0.0])
        for x in D['leads']:
            if x['lost'] and x['closed'] and sc['start'] <= x['closed'] < sc['now']:
                counts[x['lost_reason']][0] += 1
                counts[x['lost_reason']][1] += x['value']
        reasons = {r.id: r.name for r in self.env['crm.lost.reason'].sudo().browse([r for r in counts if r])}
        items = [{'label': reasons.get(rid) or _('Không ghi lý do'), 'value': c[0],
                  'sub': {'money': c[1], 'prefix': _('mất ')},
                  'drill': {'type': 'lost', 'id': rid or 0, 'title': reasons.get(rid) or _('Không ghi lý do')}}
                 for rid, c in sorted(counts.items(), key=lambda kv: -kv[1][0])]
        return {'type': 'bars', 'id': 'lost', 'title': _('Vì sao thua'), 'unit': 'count',
                'subtitle': _('Lý do mất cơ hội trong kỳ — đầu vào để sửa sản phẩm & kịch bản bán'),
                'items': items, 'empty': _('Không có cơ hội thua trong kỳ. 👏')}

    def _hot_deals(self, D):
        sc = D['sc']
        now, today = sc['now'], sc['today']
        users = {u.id: u.name for u in self.env['res.users'].sudo().browse({x['user'] for x in D['leads'] if x['user']})}
        stages = {s.id: s.name for s in self.env['crm.stage'].sudo().browse({x['stage'] for x in D['leads'] if x['stage']})}
        rows = []
        for x in self._open_leads(D):
            reasons = []
            idle = (now - x['last_stage']).days
            if idle > STALE_DAYS:
                reasons.append({'tone': 'bad', 'text': _('Đứng giai đoạn %s ngày', idle)})
            if not x['next_activity']:
                reasons.append({'tone': 'warn', 'text': _('Chưa có hoạt động kế tiếp')})
            elif x['next_activity'] < today:
                reasons.append({'tone': 'bad', 'text': _('Hoạt động trễ %s ngày', (today - x['next_activity']).days)})
            if x['deadline'] and x['deadline'] < today:
                reasons.append({'tone': 'warn', 'text': _('Quá ngày dự kiến chốt')})
            if not reasons:
                continue
            rows.append({
                'id': x['id'], 'open': {'type': 'record', 'model': 'crm.lead', 'id': x['id']},
                '_sort': -x['value'],
                'cells': {
                    'name': {'v': x['name'], 'sub': x['partner']},
                    'value': {'v': x['value']}, 'stage': stages.get(x['stage'], ''),
                    'user': users.get(x['user'], _('Chưa phân công')),
                    'idle': {'v': idle},
                    'reasons': {'v': reasons},
                }})
        rows.sort(key=lambda r: r.pop('_sort'))
        return {'type': 'table', 'id': 'deals', 'title': _('Cơ hội lớn cần chăm sóc'),
                'subtitle': _('Cơ hội đang mở có dấu hiệu nguội, xếp theo giá trị · bấm để mở cơ hội'),
                'columns': [col('name', _('Cơ hội')), col('value', _('Giá trị'), 'money'),
                            col('stage', _('Giai đoạn')), col('user', _('Phụ trách')),
                            col('idle', _('Cập nhật giai đoạn'), 'ago'), col('reasons', _('Vì sao'), 'chips')],
                'rows': rows[:40], 'limit': 8, 'empty': _('Pipeline đang được chăm sóc tốt.')}

    def _custom_signals(self, D):
        now = D['sc']['now']
        stale = [x for x in D['leads'] if self._stale_at(x, now, now)]
        out = []
        if stale:
            value = sum(x['value'] for x in stale)
            out.append({'severity': 'warning', 'title': _('Pipeline bị treo'), 'money': value,
                        'message': _('%s cơ hội đứng yên quá %s ngày.', len(stale), STALE_DAYS),
                        'drill': {'type': 'ids', 'model': 'crm.lead', 'ids': [x['id'] for x in stale],
                                  'title': _('Cơ hội nguội')}})
        old_quotes = [q for q in D['quotes'] if now - q['create'] > timedelta(days=14)]
        if old_quotes:
            out.append({'severity': 'warning', 'title': _('Báo giá treo'),
                        'money': sum(q['amount'] for q in old_quotes),
                        'message': _('%s báo giá gửi quá 14 ngày chưa chốt.', len(old_quotes)),
                        'drill': {'type': 'ids', 'model': 'sale.order', 'ids': [q['id'] for q in old_quotes],
                                  'title': _('Báo giá treo quá 14 ngày')}})
        return out

    def _summary(self, D):
        return [self._kpi(D, 'sales_amount'), self._kpi(D, 'pipeline'), self._kpi(D, 'win_rate')], None

    # ------------------------------------------------------------------ drill
    def _drill(self, spec, sc):
        kind = spec.get('type')
        start = fields.Datetime.to_string(sc['start'])
        ctx_all = {'active_test': False}
        by_stage = {'group_by': ['stage_id']}
        if kind == 'kpi':
            key = spec['key']
            name = self._dmag_metrics()[key]['label']
            if key == 'sales_amount':
                return self._list_action('sale.order', self._so_domain(sc) + [
                    ('state', '=', 'sale'), ('date_order', '>=', start)], name, {'group_by': ['user_id']})
            if key == 'opps_new':
                return self._list_action('crm.lead', self._lead_domain(sc) + [('create_date', '>=', start)],
                                         name, dict(ctx_all, **by_stage))
            if key in ('win_rate', 'opps_won', 'opps_lost', 'won_value', 'cycle_days'):
                dom = self._lead_domain(sc) + [('date_closed', '>=', start)]
                if key in ('opps_won', 'won_value', 'cycle_days'):
                    dom += [('active', '=', True), ('stage_id.is_won', '=', True)]
                elif key == 'opps_lost':
                    dom += [('active', '=', False)]
                return self._list_action('crm.lead', dom, name, dict(ctx_all, group_by=['active']))
            if key == 'pipeline':
                return self._list_action('crm.lead', self._lead_domain(sc) + [('stage_id.is_won', '=', False)],
                                         name, by_stage)
            if key == 'stale':
                D = self._load(sc, sc['start'])
                ids = [x['id'] for x in D['leads'] if self._stale_at(x, sc['now'], sc['now'])]
                return self._list_action('crm.lead', [('id', 'in', ids)], name, by_stage)
        if kind == 'stage':
            return self._list_action('crm.lead', self._lead_domain(sc) + [('stage_id', '=', int(spec['id']))],
                                     spec.get('title') or '')
        if kind == 'user':
            uid = int(spec['id'])
            dom = [('type', '=', 'opportunity'), ('stage_id.is_won', '=', False),
                   ('user_id', '=', uid or False)]
            return self._list_action('crm.lead', dom, _('Cơ hội đang mở'), by_stage)
        if kind == 'lost':
            dom = self._lead_domain(sc) + [('active', '=', False), ('date_closed', '>=', start),
                                           ('lost_reason_id', '=', int(spec['id']) or False)]
            return self._list_action('crm.lead', dom, spec.get('title') or '', ctx_all)
        return None
