from collections import defaultdict

from odoo import api, fields, models, _

from .board_base import col, dt


class DmagBoardMarketing(models.AbstractModel):
    _name = 'dmag.board.marketing'
    _inherit = 'dmag.board'
    _description = 'DMAG Board · Marketing'

    _dmag_key = 'marketing'
    _dmag_title = 'Marketing'
    _dmag_icon = 'fa-bullhorn'
    _dmag_sequence = 30
    _dmag_group = 'dmag_dashboard.group_dmag_role_marketing'
    _dmag_requires = ('crm.lead',)
    _dmag_action = 'dmag_dashboard.action_dmag_board_marketing'

    def _has_events(self):
        return 'event.registration' in self.env

    @api.model
    def _dmag_metrics(self):
        res = {
            'leads_new': {'label': 'Lead mới', 'tag': 'Thu hút', 'unit': 'count', 'good': 'up',
                          'hint': 'Lead và cơ hội được tạo trong kỳ (mọi nguồn).'},
            'qualify_rate': {'label': 'Tỷ lệ lead đủ chuẩn', 'tag': 'Chuyển đổi', 'kind': 'ratio', 'unit': 'percent',
                             'good': 'up', 'hint': 'Trong số lead tạo trong kỳ: % đã chuyển thành cơ hội bán hàng.'},
            'mkt_won_value': {'label': 'Doanh thu từ marketing', 'tag': 'Chuyển đổi', 'accent': 'navy',
                              'unit': 'money', 'good': 'up',
                              'hint': 'Giá trị cơ hội thắng trong kỳ có gắn nguồn / kênh / chiến dịch.'},
            'mkt_won': {'label': 'Cơ hội thắng từ marketing', 'unit': 'count', 'good': 'up',
                        'hint': 'Số cơ hội thắng trong kỳ có gắn nguồn / kênh / chiến dịch.'},
            'unassigned': {'label': 'Lead chưa phân công', 'tag': 'Bàn giao', 'kind': 'state', 'unit': 'count',
                           'good': 'down', 'hint': 'Lead/cơ hội đang mở chưa có nhân viên kinh doanh phụ trách.'},
        }
        if self._has_events():
            res.update({
                'event_regs': {'label': 'Đăng ký sự kiện', 'tag': 'Sự kiện', 'accent': 'cyan', 'unit': 'count',
                               'good': 'up', 'hint': 'Lượt đăng ký sự kiện/hội thảo trong kỳ (không tính hủy).'},
                'attend_rate': {'label': 'Tỷ lệ tham dự', 'tag': 'Sự kiện', 'accent': 'cyan', 'kind': 'ratio',
                                'unit': 'percent', 'good': 'up',
                                'hint': 'Với sự kiện kết thúc trong kỳ: % người đăng ký thực sự tham dự.'},
            })
        return res

    @api.model
    def _kpi_order(self):
        if self._has_events():
            return ['leads_new', 'qualify_rate', 'mkt_won_value', 'unassigned', 'event_regs', 'attend_rate']
        return ['leads_new', 'qualify_rate', 'mkt_won_value', 'mkt_won', 'unassigned']

    def _load(self, sc, since):
        D = super()._load(sc, since)
        Lead = self.env['crm.lead'].sudo().with_context(active_test=False)
        base = [('company_id', 'in', sc['company_ids'] + [False])]
        leads = Lead.search(base + ['|', '|', ('create_date', '>=', since), ('date_closed', '>=', since),
                                    '&', ('active', '=', True), ('date_closed', '=', False)])
        D['leads'] = []
        for lead in leads:
            won = lead.active and (lead.stage_id.is_won or lead.probability >= 100)
            D['leads'].append({
                'id': lead.id, 'create': dt(lead.create_date), 'won': won, 'active': lead.active,
                'opportunity': lead.type == 'opportunity', 'closed': dt(lead.date_closed),
                'value': lead.expected_revenue, 'user': lead.user_id.id,
                'source': lead.source_id.id, 'medium': lead.medium_id.id, 'campaign': lead.campaign_id.id,
            })
        D['regs'] = []
        D['events'] = self.env['event.event']
        if self._has_events():
            regs = self.env['event.registration'].sudo().search([
                ('event_id.company_id', 'in', sc['company_ids'] + [False]),
                '|', ('create_date', '>=', since), ('event_id.date_end', '>=', since)])
            D['regs'] = [{'id': r.id, 'create': dt(r.create_date), 'state': r.state, 'event': r.event_id.id,
                          'event_end': dt(r.event_id.date_end)} for r in regs]
            D['events'] = self.env['event.event'].sudo().search([
                ('company_id', 'in', sc['company_ids'] + [False]), ('date_end', '>=', sc['start'])],
                order='date_begin', limit=20)
        return D

    @staticmethod
    def _attributed(x):
        return bool(x['source'] or x['medium'] or x['campaign'])

    def _metric(self, D, key, start, end):
        leads = D['leads']
        if key == 'leads_new':
            return sum(1 for x in leads if start <= x['create'] < end)
        if key == 'qualify_rate':
            cohort = [x for x in leads if start <= x['create'] < end]
            return round(100.0 * sum(1 for x in cohort if x['opportunity']) / len(cohort), 1) if cohort else None
        if key in ('mkt_won_value', 'mkt_won'):
            won = [x for x in leads if x['won'] and x['closed'] and start <= x['closed'] < end and self._attributed(x)]
            return sum(x['value'] for x in won) if key == 'mkt_won_value' else len(won)
        if key == 'unassigned':
            return sum(1 for x in leads if x['create'] <= end and not x['user'] and x['active'] and not x['won']
                       and (not x['closed'] or x['closed'] > end))
        if key == 'event_regs':
            return sum(1 for r in D['regs'] if start <= r['create'] < end and r['state'] != 'cancel')
        if key == 'attend_rate':
            regs = [r for r in D['regs'] if r['event_end'] and start <= r['event_end'] < end
                    and r['state'] in ('open', 'done')]
            return round(100.0 * sum(1 for r in regs if r['state'] == 'done') / len(regs), 1) if regs else None
        raise ValueError(key)

    def _kpi_sub(self, D, key):
        sc = D['sc']
        start, now = sc['start'], sc['now']
        if key == 'leads_new':
            cohort = [x for x in D['leads'] if start <= x['create'] < now]
            return _('%s có nguồn rõ ràng', sum(1 for x in cohort if self._attributed(x)))
        if key == 'mkt_won_value':
            return _('%s cơ hội thắng', self._metric(D, 'mkt_won', start, now))
        if key == 'qualify_rate':
            cohort = [x for x in D['leads'] if start <= x['create'] < now]
            return _('%s/%s lead thành cơ hội', sum(1 for x in cohort if x['opportunity']), len(cohort))
        return ''

    # ------------------------------------------------------------------ panels
    def _by_dimension(self, D, field):
        sc = D['sc']
        stats = defaultdict(lambda: {'leads': 0, 'opps': 0, 'won': 0, 'value': 0.0})
        for x in D['leads']:
            k = x[field] or 0
            if sc['start'] <= x['create'] < sc['now']:
                stats[k]['leads'] += 1
                stats[k]['opps'] += x['opportunity']
            if x['won'] and x['closed'] and sc['start'] <= x['closed'] < sc['now']:
                stats[k]['won'] += 1
                stats[k]['value'] += x['value']
        return stats

    def _rows(self, D):
        model = {'source': 'utm.source', 'medium': 'utm.medium', 'campaign': 'utm.campaign'}
        names = {}
        for field, m in model.items():
            ids = {x[field] for x in D['leads'] if x[field]}
            names[field] = {r.id: r.name for r in self.env[m].sudo().browse(ids)}

        src = self._by_dimension(D, 'source')
        src_rows = []
        for sid, s in sorted(src.items(), key=lambda kv: (-kv[1]['value'], -kv[1]['leads'])):
            rate = round(100.0 * s['opps'] / s['leads']) if s['leads'] else None
            src_rows.append({
                'id': sid, 'open': {'type': 'dim', 'field': 'source_id', 'id': sid,
                                    'title': names['source'].get(sid) or _('Không rõ nguồn')},
                'cells': {
                    'name': {'v': names['source'].get(sid) or _('Không rõ nguồn'), 'tone': None if sid else 'warn'},
                    'leads': s['leads'], 'rate': {'v': rate, 'tone': None if rate is None else 'good'
                                                  if rate >= 40 else 'warn' if rate >= 20 else 'bad'},
                    'won': s['won'], 'value': {'v': s['value']},
                }})
        sources = {'type': 'table', 'id': 'sources', 'title': _('Hiệu quả theo nguồn'),
                   'subtitle': _('Lead tạo trong kỳ, % đủ chuẩn và doanh thu thắng · bấm để xem lead của nguồn'),
                   'columns': [col('name', _('Nguồn')), col('leads', _('Lead'), 'num'),
                               col('rate', _('Đủ chuẩn'), 'pct'), col('won', _('Thắng'), 'num'),
                               col('value', _('Doanh thu thắng'), 'money')],
                   'rows': src_rows, 'empty': _('Chưa có lead trong kỳ.')}

        med = self._by_dimension(D, 'medium')
        mediums = {'type': 'bars', 'id': 'mediums', 'title': _('Lead theo kênh'), 'unit': 'count',
                   'subtitle': _('Kênh nào đang mang về nhiều lead nhất'),
                   'items': [{'label': names['medium'].get(mid) or _('Không rõ kênh'), 'value': s['leads'],
                              'sub': _('%s thành cơ hội', s['opps']),
                              'drill': {'type': 'dim', 'field': 'medium_id', 'id': mid,
                                        'title': names['medium'].get(mid) or _('Không rõ kênh')}}
                             for mid, s in sorted(med.items(), key=lambda kv: -kv[1]['leads']) if s['leads']],
                   'empty': _('Chưa có lead trong kỳ.')}

        camp = self._by_dimension(D, 'campaign')
        camp_rows = [{
            'id': cid, 'open': {'type': 'dim', 'field': 'campaign_id', 'id': cid, 'title': names['campaign'].get(cid)},
            'cells': {'name': names['campaign'].get(cid), 'leads': s['leads'], 'won': s['won'],
                      'value': {'v': s['value']}}}
            for cid, s in sorted(camp.items(), key=lambda kv: (-kv[1]['value'], -kv[1]['leads'])) if cid]
        campaigns = {'type': 'table', 'id': 'campaigns', 'title': _('Chiến dịch'),
                     'subtitle': _('Lead & doanh thu thắng theo chiến dịch'),
                     'columns': [col('name', _('Chiến dịch')), col('leads', _('Lead'), 'num'),
                                 col('won', _('Thắng'), 'num'), col('value', _('Doanh thu'), 'money')],
                     'rows': camp_rows, 'empty': _('Chưa gắn chiến dịch cho lead nào trong kỳ.')}
        groups = [(_('Lead'), ['leads_new', 'mkt_won', 'mkt_won_value', 'unassigned'])]
        if self._has_events():
            groups.append((_('Sự kiện'), ['event_regs']))
        rows = [
            {'widths': [7, 5], 'panels': [sources, mediums]},
            {'widths': [7, 5], 'panels': [self._trend(D, groups), campaigns]},
        ]
        if self._has_events():
            rows.append({'widths': [12], 'panels': [self._events_panel(D)]})
        return rows

    def _events_panel(self, D):
        now = D['sc']['now']
        rows = []
        for ev in D['events']:
            seats = ev.seats_max if ev.seats_limited else 0
            taken = ev.seats_reserved + ev.seats_used
            upcoming = ev.date_begin and ev.date_begin > now
            rows.append({
                'id': ev.id, 'open': {'type': 'record', 'model': 'event.event', 'id': ev.id},
                'cells': {
                    'name': {'v': ev.name, 'sub': _('Sắp diễn ra') if upcoming else _('Đã/đang diễn ra')},
                    'date': {'v': fields.Datetime.to_string(ev.date_begin)},
                    'regs': taken, 'attended': ev.seats_used,
                    'fill': {'v': round(100.0 * taken / seats) if seats else None,
                             'sub': _('%s chỗ', seats) if seats else _('Không giới hạn chỗ')},
                }})
        return {'type': 'table', 'id': 'events', 'title': _('Sự kiện & hội thảo'),
                'subtitle': _('Sự kiện kết thúc từ đầu kỳ tới nay và sắp diễn ra'),
                'columns': [col('name', _('Sự kiện')), col('date', _('Ngày'), 'date'),
                            col('regs', _('Đăng ký'), 'num'), col('attended', _('Đã tham dự'), 'num'),
                            col('fill', _('Lấp đầy'), 'meter')],
                'rows': rows, 'empty': _('Không có sự kiện trong kỳ.')}

    def _custom_signals(self, D):
        now = D['sc']['now']
        ids = [x['id'] for x in D['leads'] if not x['user'] and x['active'] and not x['won'] and not x['closed']]
        if ids:
            return [{'severity': 'warning', 'title': _('Lead chưa có người nhận'),
                     'message': _('%s lead/cơ hội đang mở chưa được phân công cho kinh doanh.', len(ids)),
                     'drill': {'type': 'ids', 'model': 'crm.lead', 'ids': ids, 'title': _('Lead chưa phân công')}}]
        return []

    def _summary(self, D):
        return [self._kpi(D, 'leads_new'), self._kpi(D, 'qualify_rate'), self._kpi(D, 'mkt_won_value')], None

    def _drill(self, spec, sc):
        kind = spec.get('type')
        start = fields.Datetime.to_string(sc['start'])
        base = [('company_id', 'in', sc['company_ids'] + [False])]
        if kind == 'kpi':
            key = spec['key']
            name = self._dmag_metrics()[key]['label']
            if key in ('leads_new', 'qualify_rate'):
                return self._list_action('crm.lead', base + [('create_date', '>=', start)], name,
                                         {'active_test': False, 'group_by': ['type']})
            if key in ('mkt_won_value', 'mkt_won'):
                return self._list_action('crm.lead', base + [
                    ('stage_id.is_won', '=', True), ('date_closed', '>=', start),
                    '|', '|', ('source_id', '!=', False), ('medium_id', '!=', False), ('campaign_id', '!=', False)],
                    name, {'group_by': ['source_id']})
            if key == 'unassigned':
                return self._list_action('crm.lead', base + [('user_id', '=', False), ('stage_id.is_won', '=', False)],
                                         name)
            if key == 'event_regs':
                return self._list_action('event.registration', [('create_date', '>=', start),
                                                                ('state', '!=', 'cancel')], name,
                                         {'group_by': ['event_id']})
            if key == 'attend_rate':
                return self._list_action('event.registration', [('event_id.date_end', '>=', start),
                                                                ('state', 'in', ('open', 'done'))], name,
                                         {'group_by': ['event_id', 'state']})
        if kind == 'dim':
            field = spec['field']
            if field not in ('source_id', 'medium_id', 'campaign_id'):
                return None
            return self._list_action('crm.lead', base + [('create_date', '>=', start),
                                                         (field, '=', int(spec['id']) or False)],
                                     spec.get('title') or '', {'active_test': False, 'group_by': ['type']})
        return None
