from collections import defaultdict

from odoo import api, models, _

from .board_base import iso

# (board, metric) shown in the executive KPI row, in order; boards that are missing are skipped
EXEC_KPIS = [('sales', 'sales_amount'), ('finance', 'cash_in'), ('finance', 'ar_overdue'),
             ('sales', 'pipeline'), ('learning', 'contracts'), ('hr', 'headcount')]
EXEC_TREND = [('finance', ['revenue', 'cash_in']), ('sales', ['sales_amount', 'opps_new']),
              ('learning', ['lessons_done', 'tasks_done']), ('hr', ['headcount'])]


class DmagBoardExecutive(models.AbstractModel):
    _name = 'dmag.board.executive'
    _inherit = 'dmag.board'
    _description = 'DMAG Board · Tổng quan điều hành'

    _dmag_key = 'executive'
    _dmag_title = 'Tổng quan điều hành'
    _dmag_icon = 'fa-tachometer'
    _dmag_sequence = 10
    _dmag_group = 'dmag_dashboard.group_dmag_role_exec'
    _dmag_action = 'dmag_dashboard.action_dmag_board_executive'

    def _rules(self, sc):
        return self.env['dmag.alert.rule']

    @api.model
    def get_board(self, params=None):
        self._check_access()
        sc = self._scope(params)
        loaded = {}
        for board in self._boards():
            if board._dmag_key == self._dmag_key or not board._available() or not board._allowed():
                continue
            bsc = board._scope(dict(params or {}, filter_id=0))
            rules = board._rules(bsc)
            loaded[board._dmag_key] = (board, board._load(bsc, board._since(bsc, rules)), rules)

        kpis = [loaded[b][0]._kpi(loaded[b][1], key) for b, key in EXEC_KPIS if b in loaded]
        signals, cards = [], []
        for key, (board, D, rules) in loaded.items():
            sigs = board._signals(D, rules)
            signals += sigs
            metrics, extra = board._summary(D)
            cards.append({
                'key': key, 'title': board._dmag_title, 'icon': board._dmag_icon, 'action': board._dmag_action,
                'metrics': [{'label': m['label'], 'value': m['value'], 'unit': m['unit'], 'delta': m['delta'],
                             'tone': m['tone']} for m in metrics] + ([dict(extra, tone='bad' if extra['value'] else 'good')]
                                                                     if extra else []),
                'signals': {'critical': sum(1 for s in sigs if s['severity'] == 'critical'),
                            'warning': sum(1 for s in sigs if s['severity'] == 'warning'),
                            'positive': sum(1 for s in sigs if s['severity'] == 'positive')},
            })
        rank = {'critical': 0, 'warning': 1, 'positive': 2, 'info': 3}
        signals.sort(key=lambda s: rank.get(s['severity'], 9))

        series, tabs = {}, []
        for bkey, keys in EXEC_TREND:
            if bkey not in loaded:
                continue
            board, D, _rules = loaded[bkey]
            metrics = board._dmag_metrics()
            tab_keys = []
            for key in keys:
                skey = f'{bkey}.{key}'
                series[skey] = {
                    'label': metrics[key]['label'], 'unit': metrics[key]['unit'],
                    'kind': metrics[key].get('kind', 'flow'),
                    'current': board._series(D, key, D['sc']['start'], D['sc']['now']),
                    'previous': board._series(D, key, D['sc']['prev_start'], D['sc']['start']),
                }
                tab_keys.append(skey)
            tabs.append({'group': board._dmag_title, 'keys': tab_keys})
        rows = [{'widths': [12], 'panels': [{'type': 'boards', 'id': 'boards', 'title': _('Sức khỏe các phòng ban'),
                                             'subtitle': _('Bấm vào thẻ để mở bảng của phòng ban'),
                                             'items': cards}]}]
        second = []
        if series:
            second.append({'type': 'trend', 'id': 'trend', 'title': _('Nhịp doanh nghiệp'), 'series': series,
                           'tabs': tabs, 'default': next(iter(series))})
        if 'sales' in loaded:
            second.append(self._top_customers(loaded['sales'][1], 'finance' in loaded))
        if second:
            rows.append({'widths': [7, 5] if len(second) == 2 else [12], 'panels': second})
        return {
            'board': self._board_info(), 'menu': self.get_menu(), 'generated_at': iso(sc['now']),
            'period': sc['period'],
            'currency': {'symbol': sc['currency'].symbol, 'position': sc['currency'].position},
            'filter': None, 'filter_id': 0, 'kpis': kpis,
            'buckets': [iso(s) for s, _e in self._buckets(sc['start'], sc['now'])],
            'signals': signals, 'rows': rows,
            'glossary': [g for b, _D, _r in loaded.values() for g in b._glossary()
                         if any(g['key'] == k and b._dmag_key == bk for bk, k in EXEC_KPIS)],
        }

    def _top_customers(self, D, finance):
        sc = D['sc']
        totals = defaultdict(float)
        for o in D['orders']:
            if sc['start'] <= o['date'] < sc['now']:
                totals[o['customer']] += o['amount']
        names = {p.id: p.display_name for p in self.env['res.partner'].sudo().browse(list(totals))}
        items = []
        for cid, value in sorted(totals.items(), key=lambda kv: -kv[1])[:10]:
            drill = {'type': 'detail', 'kind': 'customer', 'id': cid, 'board': 'finance'} if finance else \
                {'type': 'record', 'model': 'res.partner', 'id': cid}
            items.append({'label': names[cid], 'value': value, 'drill': drill})
        return {'type': 'bars', 'id': 'top_customers', 'title': _('Khách hàng doanh số cao nhất'), 'unit': 'money',
                'subtitle': _('Đơn bán xác nhận trong kỳ · bấm để xem công nợ khách'), 'items': items,
                'empty': _('Chưa có đơn bán trong kỳ.')}
