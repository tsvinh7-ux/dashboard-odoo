from collections import defaultdict
from datetime import timedelta

from odoo import api, fields, models, _

from .board_base import col, dt


class DmagBoardOps(models.AbstractModel):
    _name = 'dmag.board.ops'
    _inherit = 'dmag.board'
    _description = 'DMAG Board · Mua hàng & Kho'

    _dmag_key = 'ops'
    _dmag_title = 'Mua hàng & Kho'
    _dmag_icon = 'fa-truck'
    _dmag_sequence = 70
    _dmag_group = 'dmag_dashboard.group_dmag_role_ops'
    _dmag_requires = ('purchase.order', 'stock.picking')
    _dmag_action = 'dmag_dashboard.action_dmag_board_ops'

    @api.model
    def _dmag_metrics(self):
        return {
            'purchase_value': {'label': 'Giá trị mua hàng', 'tag': 'Mua hàng', 'accent': 'navy', 'unit': 'money',
                               'good': None, 'hint': 'Đơn mua được xác nhận trong kỳ, trước thuế.'},
            'po_count': {'label': 'Đơn mua xác nhận', 'tag': 'Mua hàng', 'accent': 'navy', 'unit': 'count',
                         'good': None, 'hint': 'Số đơn mua được xác nhận trong kỳ.'},
            'late_receipts': {'label': 'Phiếu nhận trễ', 'tag': 'Kho', 'kind': 'state', 'unit': 'count',
                              'good': 'down', 'hint': 'Phiếu nhập hàng chưa hoàn tất đã quá ngày dự kiến (tại cuối kỳ).'},
            'late_deliveries': {'label': 'Phiếu giao trễ', 'tag': 'Kho', 'kind': 'state', 'unit': 'count',
                                'good': 'down', 'hint': 'Phiếu giao hàng chưa hoàn tất đã quá ngày dự kiến (tại cuối kỳ).'},
            'supplier_otd': {'label': 'NCC giao đúng hạn', 'tag': 'Mua hàng', 'kind': 'ratio', 'unit': 'percent',
                             'good': 'up',
                             'hint': 'Phiếu nhận hoàn tất trong kỳ: % hoàn tất trước/đúng ngày dự kiến (dung sai 1 ngày).'},
            'below_min': {'label': 'Dưới mức tồn tối thiểu', 'tag': 'Kho', 'accent': 'cyan', 'kind': 'snapshot',
                          'unit': 'count', 'good': 'down',
                          'hint': 'Quy tắc tồn kho có số lượng dự báo thấp hơn mức tối thiểu (hiện tại).'},
            'receipts_done': {'label': 'Phiếu nhận hoàn tất', 'unit': 'count', 'good': 'up',
                              'hint': 'Phiếu nhập hàng hoàn tất trong kỳ.'},
            'deliveries_done': {'label': 'Phiếu giao hoàn tất', 'unit': 'count', 'good': 'up',
                                'hint': 'Phiếu giao hàng hoàn tất trong kỳ.'},
        }

    @api.model
    def _kpi_order(self):
        return ['purchase_value', 'po_count', 'supplier_otd', 'late_receipts', 'late_deliveries', 'below_min']

    def _load(self, sc, since):
        D = super()._load(sc, since)
        env = self.env
        pos = env['purchase.order'].sudo().search([
            ('company_id', 'in', sc['company_ids']), ('state', 'in', ('purchase', 'done')),
            ('date_approve', '>=', since)])
        D['pos'] = [{'id': p.id, 'date': dt(p.date_approve), 'partner': p.partner_id.commercial_partner_id.id,
                     'amount': self._money(p.amount_untaxed, p.currency_id, p.date_approve.date(), sc)} for p in pos]
        picks = env['stock.picking'].sudo().search([
            ('company_id', 'in', sc['company_ids']), ('picking_type_code', 'in', ('incoming', 'outgoing')),
            ('state', '!=', 'cancel'), '|', ('date_done', '>=', since), ('state', 'not in', ('done', 'cancel'))])
        D['picks'] = [{'id': p.id, 'name': p.name, 'code': p.picking_type_code, 'create': dt(p.create_date),
                       'scheduled': dt(p.date_deadline or p.scheduled_date), 'done': dt(p.date_done)
                       if p.state == 'done' else None, 'partner': p.partner_id.display_name or '',
                       'origin': p.origin or ''} for p in picks]
        D['orderpoints'] = []
        if 'stock.warehouse.orderpoint' in env:
            ops = env['stock.warehouse.orderpoint'].sudo().search([('company_id', 'in', sc['company_ids'])])
            D['orderpoints'] = [o for o in ops if o.qty_forecast < o.product_min_qty]
        return D

    def _late(self, D, code, t):
        return [p for p in D['picks'] if p['code'] == code and p['create'] <= t and p['scheduled']
                and p['scheduled'] < t and (not p['done'] or p['done'] > t)]

    def _metric(self, D, key, start, end):
        if key == 'purchase_value':
            return sum(p['amount'] for p in D['pos'] if start <= p['date'] < end)
        if key == 'po_count':
            return sum(1 for p in D['pos'] if start <= p['date'] < end)
        if key == 'late_receipts':
            return len(self._late(D, 'incoming', end))
        if key == 'late_deliveries':
            return len(self._late(D, 'outgoing', end))
        if key in ('receipts_done', 'deliveries_done'):
            code = 'incoming' if key == 'receipts_done' else 'outgoing'
            return sum(1 for p in D['picks'] if p['code'] == code and p['done'] and start <= p['done'] < end)
        if key == 'supplier_otd':
            done = [p for p in D['picks'] if p['code'] == 'incoming' and p['done'] and start <= p['done'] < end
                    and p['scheduled']]
            if not done:
                return None
            ok = sum(1 for p in done if p['done'] <= p['scheduled'] + timedelta(days=1))
            return round(100.0 * ok / len(done), 1)
        if key == 'below_min':
            return len(D['orderpoints'])
        raise ValueError(key)

    def _kpi_sub(self, D, key):
        sc = D['sc']
        if key == 'purchase_value':
            return _('%s nhà cung cấp', len({p['partner'] for p in D['pos'] if sc['start'] <= p['date'] < sc['now']}))
        if key == 'late_receipts':
            rows = self._late(D, 'incoming', sc['now'])
            worst = max(((sc['now'] - p['scheduled']).days for p in rows), default=0)
            return _('trễ nhất %s ngày', worst) if rows else _('Không có phiếu trễ')
        if key == 'below_min':
            return _('cần đặt thêm %s mã hàng', len(D['orderpoints'])) if D['orderpoints'] else _('Tồn kho an toàn')
        return ''

    def _rows(self, D):
        sc = D['sc']
        spend = defaultdict(float)
        for p in D['pos']:
            if sc['start'] <= p['date'] < sc['now']:
                spend[p['partner']] += p['amount']
        partners = {p.id: p.display_name for p in self.env['res.partner'].sudo().browse(list(spend))}
        suppliers = {'type': 'bars', 'id': 'suppliers', 'title': _('Nhà cung cấp lớn nhất'), 'unit': 'money',
                     'subtitle': _('Giá trị mua trong kỳ · bấm để xem đơn mua'),
                     'items': [{'label': partners[pid], 'value': v,
                                'drill': {'type': 'supplier', 'id': pid, 'title': partners[pid]}}
                               for pid, v in sorted(spend.items(), key=lambda kv: -kv[1])[:10]],
                     'empty': _('Chưa có đơn mua trong kỳ.')}
        trend = self._trend(D, [(_('Mua hàng'), ['purchase_value', 'po_count']),
                                (_('Kho'), ['receipts_done', 'deliveries_done', 'late_receipts', 'late_deliveries'])])
        now = sc['now']
        late_rows = []
        for p in self._late(D, 'incoming', now) + self._late(D, 'outgoing', now):
            days = max(1, (now - p['scheduled']).days)
            late_rows.append({'id': p['id'], 'open': {'type': 'record', 'model': 'stock.picking', 'id': p['id']},
                              '_sort': -days,
                              'cells': {'name': {'v': p['name'], 'sub': p['origin']}, 'partner': p['partner'],
                                        'kind': {'v': _('Nhận hàng') if p['code'] == 'incoming' else _('Giao hàng'),
                                                 'tone': 'info' if p['code'] == 'incoming' else None},
                                        'late': {'v': days, 'tone': 'bad' if days > 7 else 'warn'}}})
        late_rows.sort(key=lambda r: r.pop('_sort'))
        late = {'type': 'table', 'id': 'late', 'title': _('Phiếu kho trễ hạn'),
                'subtitle': _('Nhận/giao hàng quá ngày dự kiến chưa hoàn tất · bấm để mở phiếu'),
                'columns': [col('name', _('Phiếu')), col('partner', _('Đối tác')), col('kind', _('Loại')),
                            col('late', _('Trễ'), 'days')],
                'rows': late_rows, 'limit': 8, 'empty': _('Không có phiếu trễ. 👏')}
        stock_rows = [{'id': o.id, 'open': {'type': 'record', 'model': 'stock.warehouse.orderpoint', 'id': o.id},
                       'cells': {'name': {'v': o.product_id.display_name, 'sub': o.warehouse_id.name},
                                 'forecast': {'v': o.qty_forecast, 'tone': 'bad' if o.qty_forecast <= 0 else 'warn'},
                                 'min': o.product_min_qty, 'order': o.qty_to_order}}
                      for o in sorted(D['orderpoints'], key=lambda o: o.qty_forecast - o.product_min_qty)]
        stock = {'type': 'table', 'id': 'stock', 'title': _('Hàng dưới mức tối thiểu'),
                 'subtitle': _('Theo quy tắc tồn kho tối thiểu/tối đa'),
                 'columns': [col('name', _('Sản phẩm')), col('forecast', _('Dự báo'), 'num'),
                             col('min', _('Tối thiểu'), 'num'), col('order', _('Cần đặt'), 'num')],
                 'rows': stock_rows, 'limit': 8, 'empty': _('Tồn kho an toàn.')}
        return [{'widths': [5, 7], 'panels': [suppliers, trend]},
                {'widths': [7, 5], 'panels': [late, stock]}]

    def _summary(self, D):
        return [self._kpi(D, 'purchase_value'), self._kpi(D, 'late_receipts'), self._kpi(D, 'below_min')], None

    def _drill(self, spec, sc):
        kind = spec.get('type')
        start = fields.Datetime.to_string(sc['start'])
        now = fields.Datetime.to_string(sc['now'])
        if kind == 'kpi':
            key = spec['key']
            name = self._dmag_metrics()[key]['label']
            if key in ('purchase_value', 'po_count'):
                return self._list_action('purchase.order', [('state', 'in', ('purchase', 'done')),
                                                            ('date_approve', '>=', start)], name,
                                         {'group_by': ['partner_id']})
            if key in ('late_receipts', 'late_deliveries'):
                code = 'incoming' if key == 'late_receipts' else 'outgoing'
                return self._list_action('stock.picking', [('picking_type_code', '=', code),
                                                           ('state', 'not in', ('done', 'cancel')),
                                                           ('scheduled_date', '<', now)], name)
            if key in ('supplier_otd', 'receipts_done', 'deliveries_done'):
                code = 'outgoing' if key == 'deliveries_done' else 'incoming'
                return self._list_action('stock.picking', [('picking_type_code', '=', code), ('state', '=', 'done'),
                                                           ('date_done', '>=', start)], name,
                                         {'group_by': ['partner_id']})
            if key == 'below_min':
                D = self._load(sc, sc['start'])
                return self._list_action('stock.warehouse.orderpoint', [('id', 'in', [o.id for o in D['orderpoints']])],
                                         name)
        if kind == 'supplier':
            return self._list_action('purchase.order', [('partner_id', 'child_of', int(spec['id'])),
                                                        ('date_approve', '>=', start)], spec.get('title') or '')
        return None
