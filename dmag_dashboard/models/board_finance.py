from collections import defaultdict
from datetime import timedelta

from odoo import api, fields, models, _

from .board_base import col, dt, iso, to_dt

AGING = [(None, 0, 'Chưa đến hạn', 'good'), (1, 30, '1–30 ngày', 'info'), (31, 60, '31–60 ngày', 'warn'),
         (61, 90, '61–90 ngày', 'warn'), (91, None, '> 90 ngày', 'bad')]


class DmagBoardFinance(models.AbstractModel):
    _name = 'dmag.board.finance'
    _inherit = 'dmag.board'
    _description = 'DMAG Board · Tài chính'

    _dmag_key = 'finance'
    _dmag_title = 'Tài chính'
    _dmag_icon = 'fa-money'
    _dmag_sequence = 50
    _dmag_group = 'dmag_dashboard.group_dmag_role_finance'
    _dmag_requires = ('account.move', 'account.payment')
    _dmag_action = 'dmag_dashboard.action_dmag_board_finance'

    @api.model
    def _dmag_metrics(self):
        return {
            'revenue': {'label': 'Doanh thu xuất hóa đơn', 'tag': 'Doanh thu', 'accent': 'navy', 'unit': 'money',
                        'good': 'up', 'hint': 'Hóa đơn bán (trừ hoàn tiền) đã ghi sổ trong kỳ, trước thuế.'},
            'cash_in': {'label': 'Tiền thu về', 'tag': 'Dòng tiền', 'unit': 'money', 'good': 'up',
                        'hint': 'Thanh toán nhận từ khách hàng đã ghi sổ trong kỳ.'},
            'cash_out': {'label': 'Tiền chi ra', 'tag': 'Dòng tiền', 'unit': 'money', 'good': None,
                         'hint': 'Thanh toán chi cho nhà cung cấp / hoàn tiền đã ghi sổ trong kỳ.'},
            'ar_overdue': {'label': 'Công nợ quá hạn', 'tag': 'Công nợ', 'accent': 'cyan', 'kind': 'state',
                           'unit': 'money', 'good': 'down',
                           'hint': 'Phần chưa thu của hóa đơn đã quá hạn thanh toán (tại cuối kỳ).'},
            'ar_total': {'label': 'Tổng phải thu', 'tag': 'Công nợ', 'kind': 'state', 'unit': 'money',
                         'good': None, 'hint': 'Tổng phần chưa thu của hóa đơn bán (tại cuối kỳ).'},
            'dso': {'label': 'Số ngày thu tiền (DSO)', 'tag': 'Công nợ', 'accent': 'cyan', 'kind': 'state',
                    'unit': 'days', 'good': 'down',
                    'hint': 'Tổng phải thu / doanh thu 90 ngày gần nhất × 90 — trung bình bao nhiêu ngày thì thu được tiền.'},
            'collect_rate': {'label': 'Thu đúng hạn', 'tag': 'Công nợ', 'accent': 'cyan', 'kind': 'ratio',
                             'unit': 'percent', 'good': 'up',
                             'hint': 'Trong các hóa đơn đến hạn trong kỳ: % đã thu đủ trước/đúng hạn.'},
        }

    @api.model
    def _kpi_order(self):
        return ['revenue', 'cash_in', 'cash_out', 'ar_overdue', 'dso', 'collect_rate']

    def _filter(self, sc):
        users = self.env['res.users'].search([('share', '=', False)], limit=300)
        return {'label': _('Mọi nhân viên KD'), 'options': self._users_options(users)}

    def _inv_domain(self, sc):
        dom = [('state', '=', 'posted'), ('move_type', 'in', ('out_invoice', 'out_refund')),
               ('company_id', 'in', sc['company_ids'])]
        if sc['filter_id']:
            dom.append(('invoice_user_id', '=', sc['filter_id']))
        return dom

    def _load(self, sc, since):
        D = super()._load(sc, since)
        env = self.env
        Move = env['account.move'].sudo()
        Partial = env['account.partial.reconcile'].sudo()
        # history window: DSO needs revenue of the 90 days before the earliest point we evaluate
        hist = since - timedelta(days=90)
        moves = Move.search(self._inv_domain(sc) + [
            '|', ('invoice_date', '>=', hist.date()), ('amount_residual', '!=', 0)])
        paid_late = Partial.search([('max_date', '>=', hist.date()), ('company_id', 'in', sc['company_ids']),
                                    ('debit_move_id.move_id.move_type', '=', 'out_invoice')])
        moves |= paid_late.debit_move_id.move_id.filtered_domain(self._inv_domain(sc))
        recv = moves.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')
        pays = defaultdict(list)
        for p in Partial.search([('debit_move_id', 'in', recv.ids)]):
            pays[p.debit_move_id.move_id.id].append((to_dt(p.max_date), p.amount))
        D['invoices'] = [{
            'id': m.id, 'name': m.name, 'refund': m.move_type == 'out_refund',
            'date': to_dt(m.invoice_date), 'due': to_dt(m.invoice_date_due or m.invoice_date),
            'untaxed': m.amount_untaxed_signed, 'total': m.amount_total_signed,
            'residual': m.amount_residual_signed, 'customer': m.commercial_partner_id.id,
            'user': m.invoice_user_id.id, 'pays': sorted(pays[m.id]),
        } for m in moves]
        D['inv_ids_period'] = [i['id'] for i in D['invoices'] if i['date'] and i['date'] >= sc['start']]

        payments = env['account.payment'].sudo().search([
            ('state', '=', 'posted'), ('is_internal_transfer', '=', False),
            ('company_id', 'in', sc['company_ids']), ('date', '>=', since.date())])
        D['payments'] = [{'id': p.id, 'date': to_dt(p.date), 'type': p.payment_type,
                          'partner_type': p.partner_type, 'partner': p.partner_id.commercial_partner_id.id,
                          'amount': abs(p.amount_company_currency_signed)} for p in payments]
        bills = Move.search([('state', '=', 'posted'), ('move_type', '=', 'in_invoice'),
                             ('company_id', 'in', sc['company_ids']), ('amount_residual', '!=', 0)],
                            order='invoice_date_due asc')
        D['bills'] = [{'id': b.id, 'name': b.name or b.ref, 'partner': b.partner_id.display_name,
                       'due': b.invoice_date_due, 'residual': abs(b.amount_residual_signed)} for b in bills]
        D['expenses'] = []
        if 'hr.expense.sheet' in env:
            sheets = env['hr.expense.sheet'].sudo().search([('state', '=', 'submit'),
                                                            ('company_id', 'in', sc['company_ids'])])
            D['expenses'] = [{'id': s.id, 'amount': s.total_amount} for s in sheets]
        return D

    # ------------------------------------------------------------------ metrics
    @staticmethod
    def _residual_at(inv, t):
        paid = sum(a for d, a in inv['pays'] if d and d <= t)
        return max(0.0, inv['total'] - paid)

    def _ar(self, D, t, overdue_only=False):
        total = 0.0
        for inv in D['invoices']:
            if inv['refund'] or not inv['date'] or inv['date'] > t:
                continue
            if overdue_only and not (inv['due'] and inv['due'] < t - timedelta(days=1)):
                continue
            total += self._residual_at(inv, t)
        return total

    def _metric(self, D, key, start, end):
        if key == 'revenue':
            return sum(i['untaxed'] for i in D['invoices'] if i['date'] and start <= i['date'] < end)
        if key == 'cash_in':
            return sum(p['amount'] for p in D['payments'] if p['type'] == 'inbound' and start <= p['date'] < end)
        if key == 'cash_out':
            return sum(p['amount'] for p in D['payments'] if p['type'] == 'outbound' and start <= p['date'] < end)
        if key == 'ar_overdue':
            return self._ar(D, end, overdue_only=True)
        if key == 'ar_total':
            return self._ar(D, end)
        if key == 'dso':
            rev = self._metric(D, 'revenue', end - timedelta(days=90), end)
            return round(self._ar(D, end) / rev * 90, 1) if rev > 0 else None
        if key == 'collect_rate':
            due = [i for i in D['invoices'] if not i['refund'] and i['due'] and start <= i['due'] < end
                   and i['total'] > 0]
            if not due:
                return None
            ok = sum(1 for i in due if self._residual_at(i, i['due'] + timedelta(days=1)) <= 0.5)
            return round(100.0 * ok / len(due), 1)
        raise ValueError(key)

    def _kpi_sub(self, D, key):
        sc = D['sc']
        start, now = sc['start'], sc['now']
        if key == 'revenue':
            return _('%s hóa đơn', sum(1 for i in D['invoices'] if i['date'] and start <= i['date'] < now
                                       and not i['refund']))
        if key == 'cash_in':
            return {'money': self._metric(D, 'cash_in', start, now) - self._metric(D, 'cash_out', start, now),
                    'prefix': _('Dòng tiền ròng ')}
        if key == 'ar_overdue':
            return {'money': self._ar(D, now), 'prefix': _('trên tổng phải thu ')}
        if key == 'collect_rate':
            due = [i for i in D['invoices'] if not i['refund'] and i['due'] and start <= i['due'] < now]
            return _('%s hóa đơn đến hạn trong kỳ', len(due))
        return ''

    # ------------------------------------------------------------------ panels
    def _open_invoices(self, D):
        return [i for i in D['invoices'] if not i['refund'] and i['residual'] > 0.5]

    def _rows(self, D):
        sc = D['sc']
        now, today = sc['now'], sc['today']
        buckets = [{'label': label, 'value': 0.0, 'tone': tone, 'ids': []} for _a, _b, label, tone in AGING]
        for inv in self._open_invoices(D):
            late = (today - inv['due'].date()).days if inv['due'] else 0
            for idx, (lo, hi, _label, _tone) in enumerate(AGING):
                if (lo is None and late <= 0) or (lo is not None and late >= lo and (hi is None or late <= hi)):
                    buckets[idx]['value'] += inv['residual']
                    buckets[idx]['ids'].append(inv['id'])
                    break
        for b in buckets:
            b['drill'] = {'type': 'ids', 'model': 'account.move', 'ids': b.pop('ids'),
                          'title': _('Phải thu · %s', b['label'])}
        due_7 = [b for b in D['bills'] if b['due'] and b['due'] <= today + timedelta(days=7)]
        tiles = [
            {'label': _('Tổng phải thu'), 'value': sum(b['value'] for b in buckets), 'unit': 'money'},
            {'label': _('Phải trả đến hạn 7 ngày'), 'value': sum(b['residual'] for b in due_7), 'unit': 'money',
             'alert': bool(due_7), 'drill': {'type': 'ids', 'model': 'account.move', 'ids': [b['id'] for b in due_7],
                                             'title': _('Hóa đơn NCC đến hạn 7 ngày')}},
        ]
        if 'hr.expense.sheet' in self.env:
            tiles.append({'label': _('Đề nghị chi chờ duyệt'), 'value': sum(e['amount'] for e in D['expenses']),
                          'unit': 'money', 'alert': bool(D['expenses']),
                          'drill': {'type': 'ids', 'model': 'hr.expense.sheet',
                                    'ids': [e['id'] for e in D['expenses']], 'title': _('Đề nghị chi chờ duyệt')}})
        aging = {'type': 'aging', 'id': 'aging', 'title': _('Tuổi nợ phải thu'),
                 'subtitle': _('Phần chưa thu theo số ngày quá hạn · bấm từng nhóm để xem hóa đơn'),
                 'unit': 'money', 'buckets': buckets, 'tiles': tiles}
        trend = self._trend(D, [(_('Dòng tiền'), ['revenue', 'cash_in', 'cash_out']),
                                (_('Công nợ'), ['ar_overdue', 'ar_total'])])
        return [
            {'widths': [5, 7], 'panels': [aging, trend]},
            {'widths': [7, 5], 'panels': [self._debtors(D), self._payables(D)]},
            {'widths': [6, 6], 'panels': [self._revenue_by_category(D), self._revenue_by_user(D)]},
        ]

    def _debtors(self, D):
        sc = D['sc']
        today = sc['today']
        stats = defaultdict(lambda: {'overdue': 0.0, 'total': 0.0, 'count': 0, 'oldest': 0, 'user': 0})
        for inv in self._open_invoices(D):
            s = stats[inv['customer']]
            s['total'] += inv['residual']
            s['user'] = s['user'] or inv['user']
            late = (today - inv['due'].date()).days if inv['due'] else 0
            if late > 0:
                s['overdue'] += inv['residual']
                s['count'] += 1
                s['oldest'] = max(s['oldest'], late)
        last_pay = {}
        for p in D['payments']:
            if p['type'] == 'inbound':
                last_pay[p['partner']] = max(last_pay.get(p['partner'], p['date']), p['date'])
        partners = {p.id: p for p in self.env['res.partner'].sudo().browse(list(stats))}
        users = {u.id: u.name for u in self.env['res.users'].sudo().browse({s['user'] for s in stats.values() if s['user']})}
        rows = []
        for cid, s in sorted(stats.items(), key=lambda kv: -kv[1]['overdue']):
            if s['overdue'] <= 0:
                continue
            rows.append({
                'id': cid, 'open': {'type': 'detail', 'kind': 'customer', 'id': cid},
                'cells': {
                    'name': {'v': partners[cid].display_name, 'sub': users.get(s['user'], '')},
                    'overdue': {'v': s['overdue'], 'tone': 'bad'},
                    'oldest': {'v': s['oldest'], 'tone': 'bad' if s['oldest'] > 60 else 'warn'},
                    'count': s['count'], 'total': {'v': s['total']},
                    'last': {'v': (sc['now'] - last_pay[cid]).days if cid in last_pay else None},
                }})
        return {'type': 'table', 'id': 'debtors', 'title': _('Khách nợ quá hạn'),
                'subtitle': _('Xếp theo số tiền quá hạn · bấm để xem chi tiết công nợ từng khách'),
                'columns': [col('name', _('Khách hàng')), col('overdue', _('Quá hạn'), 'money'),
                            col('oldest', _('Lâu nhất'), 'days'), col('count', _('Số HĐ'), 'num'),
                            col('total', _('Tổng nợ'), 'money'), col('last', _('Lần thu gần nhất'), 'ago')],
                'rows': rows, 'limit': 8, 'empty': _('Không có khách nợ quá hạn. 👏')}

    def _payables(self, D):
        today = D['sc']['today']
        rows = []
        for b in D['bills']:
            if not b['due'] or b['due'] > today + timedelta(days=14):
                continue
            days = (b['due'] - today).days
            rows.append({'id': b['id'], 'open': {'type': 'record', 'model': 'account.move', 'id': b['id']},
                         'cells': {'name': {'v': b['partner'], 'sub': b['name']},
                                   'due': {'v': _('Quá %s ngày', -days) if days < 0 else _('hôm nay') if days == 0
                                           else _('còn %s ngày', days),
                                           'tone': 'bad' if days < 0 else 'warn' if days <= 3 else None},
                                   'amount': {'v': b['residual']}}})
        return {'type': 'table', 'id': 'payables', 'title': _('Phải trả sắp đến hạn'),
                'subtitle': _('Hóa đơn nhà cung cấp quá hạn hoặc đến hạn trong 14 ngày'),
                'columns': [col('name', _('Nhà cung cấp')), col('due', _('Hạn')),
                            col('amount', _('Còn phải trả'), 'money')],
                'rows': rows, 'limit': 8, 'empty': _('Không có khoản phải trả gấp.')}

    def _revenue_lines(self, D):
        if not D['inv_ids_period']:
            return []
        return self.env['account.move.line'].sudo()._read_group(
            [('move_id', 'in', D['inv_ids_period']), ('display_type', '=', 'product'),
             ('move_id.invoice_date', '>=', D['sc']['start'].date())],
            ['product_id'], ['balance:sum'])

    def _revenue_by_category(self, D):
        totals = defaultdict(float)
        for product, balance in self._revenue_lines(D):
            totals[product.categ_id.id] += -balance
        categs = {c.id: c.display_name for c in self.env['product.category'].sudo().browse([c for c in totals if c])}
        items = [{'label': categs.get(cid) or _('Không có sản phẩm'), 'value': v,
                  'drill': {'type': 'categ', 'id': cid, 'title': categs.get(cid) or ''}}
                 for cid, v in sorted(totals.items(), key=lambda kv: -kv[1]) if v]
        return {'type': 'bars', 'id': 'by_categ', 'title': _('Doanh thu theo nhóm sản phẩm/dịch vụ'),
                'subtitle': _('Hóa đơn ghi sổ trong kỳ, trước thuế'), 'unit': 'money', 'items': items[:10],
                'empty': _('Chưa có doanh thu trong kỳ.')}

    def _revenue_by_user(self, D):
        sc = D['sc']
        totals = defaultdict(float)
        for i in D['invoices']:
            if i['date'] and sc['start'] <= i['date'] < sc['now']:
                totals[i['user']] += i['untaxed']
        names = {u.id: u.name for u in self.env['res.users'].sudo().browse([u for u in totals if u])}
        items = [{'label': names.get(uid) or _('Chưa gán nhân viên'), 'value': v,
                  'drill': {'type': 'user', 'id': uid, 'title': names.get(uid) or ''}}
                 for uid, v in sorted(totals.items(), key=lambda kv: -kv[1]) if v]
        return {'type': 'bars', 'id': 'by_user', 'title': _('Doanh thu theo nhân viên kinh doanh'),
                'subtitle': _('Hóa đơn ghi sổ trong kỳ, trước thuế'), 'unit': 'money', 'items': items[:10],
                'empty': _('Chưa có doanh thu trong kỳ.')}

    def _custom_signals(self, D):
        today = D['sc']['today']
        out = []
        severe = [i for i in self._open_invoices(D) if i['due'] and (today - i['due'].date()).days > 60]
        if severe:
            out.append({'severity': 'critical', 'title': _('Nợ khó đòi'), 'money': sum(i['residual'] for i in severe),
                        'message': _('%s hóa đơn quá hạn trên 60 ngày.', len(severe)),
                        'drill': {'type': 'ids', 'model': 'account.move', 'ids': [i['id'] for i in severe],
                                  'title': _('Hóa đơn quá hạn > 60 ngày')}})
        overdue_bills = [b for b in D['bills'] if b['due'] and b['due'] < today]
        if overdue_bills:
            out.append({'severity': 'warning', 'title': _('Nợ nhà cung cấp quá hạn'),
                        'money': sum(b['residual'] for b in overdue_bills),
                        'message': _('%s hóa đơn NCC đã quá hạn thanh toán.', len(overdue_bills)),
                        'drill': {'type': 'ids', 'model': 'account.move', 'ids': [b['id'] for b in overdue_bills],
                                  'title': _('Hóa đơn NCC quá hạn')}})
        return out

    def _summary(self, D):
        return [self._kpi(D, 'revenue'), self._kpi(D, 'cash_in'), self._kpi(D, 'ar_overdue')], None

    # ------------------------------------------------------------------ drill & detail
    def _drill(self, spec, sc):
        kind = spec.get('type')
        start = sc['start'].date()
        if kind == 'kpi':
            key = spec['key']
            name = self._dmag_metrics()[key]['label']
            if key == 'revenue':
                return self._list_action('account.move', self._inv_domain(sc) + [('invoice_date', '>=', start)],
                                         name, {'group_by': ['partner_id']})
            if key in ('cash_in', 'cash_out'):
                return self._list_action('account.payment', [
                    ('state', '=', 'posted'), ('is_internal_transfer', '=', False), ('date', '>=', start),
                    ('payment_type', '=', 'inbound' if key == 'cash_in' else 'outbound'),
                    ('company_id', 'in', sc['company_ids'])], name, {'group_by': ['partner_id']})
            if key in ('ar_overdue', 'ar_total', 'dso'):
                dom = self._inv_domain(sc) + [('move_type', '=', 'out_invoice'), ('amount_residual', '>', 0)]
                if key == 'ar_overdue':
                    dom.append(('invoice_date_due', '<', sc['today']))
                return self._list_action('account.move', dom, name, {'group_by': ['partner_id']})
            if key == 'collect_rate':
                return self._list_action('account.move', self._inv_domain(sc) + [
                    ('move_type', '=', 'out_invoice'), ('invoice_date_due', '>=', start),
                    ('invoice_date_due', '<=', sc['today'])], name, {'group_by': ['payment_state']})
        if kind == 'categ':
            return self._list_action('account.move.line', [
                ('move_id', 'in', self._load(sc, sc['start'])['inv_ids_period']), ('display_type', '=', 'product'),
                ('product_id.categ_id', '=', int(spec['id']) or False)], spec.get('title') or '',
                {'group_by': ['product_id']})
        if kind == 'user':
            return self._list_action('account.move', self._inv_domain(dict(sc, filter_id=0)) + [
                ('invoice_date', '>=', start), ('invoice_user_id', '=', int(spec['id']) or False)],
                spec.get('title') or '', {'group_by': ['partner_id']})
        return None

    def _detail(self, kind, res_id, sc):
        partner = self.env['res.partner'].sudo().browse(res_id).commercial_partner_id
        D = self._load(sc, sc['prev_start'])
        today = sc['today']
        invoices = [i for i in self._open_invoices(D) if i['customer'] == partner.id]
        invoices.sort(key=lambda i: i['due'] or i['date'])
        overdue = [i for i in invoices if i['due'] and i['due'].date() < today]
        oldest = max(((today - i['due'].date()).days for i in overdue), default=0)
        items = []
        for i in invoices:
            late = (today - i['due'].date()).days if i['due'] else 0
            items.append({
                'title': i['name'], 'sub': _('Ngày HĐ %s · hạn %s', i['date'].strftime('%d/%m/%Y'),
                                             i['due'].strftime('%d/%m/%Y')),
                'badge': {'tone': 'bad' if late > 30 else 'warn', 'text': _('Quá %s ngày', late)} if late > 0 else None,
                'value': {'v': i['residual'], 'unit': 'money'},
                'drill': {'type': 'record', 'model': 'account.move', 'id': i['id']}})
        pays = self.env['account.payment'].sudo().search([
            ('partner_id', 'child_of', partner.id), ('state', '=', 'posted'), ('payment_type', '=', 'inbound')],
            order='date desc', limit=8)
        pay_items = [{'title': p.name, 'sub': p.date.strftime('%d/%m/%Y'),
                      'value': {'v': abs(p.amount_company_currency_signed), 'unit': 'money'},
                      'drill': {'type': 'record', 'model': 'account.payment', 'id': p.id}} for p in pays]
        total = sum(i['residual'] for i in invoices)
        return {
            'eyebrow': _('Công nợ khách hàng'), 'title': partner.display_name,
            'subtitle': ' · '.join(filter(None, [_('Kinh doanh: %s', partner.user_id.name) if partner.user_id else '',
                                                 partner.phone or partner.mobile, partner.email])),
            'actions': [{'label': _('Mở hồ sơ trong Odoo'), 'icon': 'fa-external-link',
                         'drill': {'type': 'record', 'model': 'res.partner', 'id': partner.id}},
                        {'label': _('Tất cả hóa đơn đang mở'), 'icon': 'fa-list',
                         'drill': {'type': 'ids', 'model': 'account.move', 'ids': [i['id'] for i in invoices],
                                   'title': partner.display_name}}],
            'stats': [{'label': _('Tổng nợ'), 'value': total, 'unit': 'money'},
                      {'label': _('Quá hạn'), 'value': sum(i['residual'] for i in overdue), 'unit': 'money',
                       'tone': 'bad' if overdue else None},
                      {'label': _('Lâu nhất'), 'value': oldest, 'unit': 'days', 'tone': 'bad' if oldest > 60 else None},
                      {'label': _('Số HĐ mở'), 'value': len(invoices), 'unit': 'count'}],
            'sections': [{'title': _('Hóa đơn chưa thu'), 'items': items, 'empty': _('Không còn công nợ.')},
                         {'title': _('Thanh toán gần đây'), 'items': pay_items, 'empty': _('Chưa có thanh toán.')}],
        }
