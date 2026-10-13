"""Generic engine shared by every DMAG board.

A board is an AbstractModel named ``dmag.board.<key>`` that inherits ``dmag.board`` and declares:

* identity (``_dmag_key``, ``_dmag_title`` ...) and the role group allowed to open it;
* ``_dmag_metrics()``: the metric catalog — every KPI, sparkline, trend series, alert rule and
  KPI drill-down of the board reads the same ``_metric()`` implementation;
* ``_load(sc, since)``: read every raw row once, so metrics are cheap pure-Python passes;
* ``_rows(D)``: the panels below the KPI row (funnel, trend, table, bars, aging, boards);
* ``_drill(spec, sc)`` / ``_detail(kind, id, sc)``: drill-down to native lists / 360° drawers.

The OWL client renders any board from that JSON contract, so a new board needs no JS.
"""
import math
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError

# models the generic "record" / "ids" drill may open (native views keep the user's ACLs)
DRILL_MODELS = {
    'res.partner', 'sale.order', 'project.task', 'project.project', 'slide.channel', 'slide.channel.partner',
    'crm.lead', 'account.move', 'account.payment', 'hr.employee', 'hr.applicant', 'hr.job', 'hr.contract',
    'purchase.order', 'stock.picking', 'stock.warehouse.orderpoint', 'event.event', 'event.registration',
    'hr.expense.sheet', 'product.product',
}


def dt(value):
    """ORM 'now' has no microseconds while create/write dates do: normalise so a record created
    during the current second is never considered to be in the future."""
    return value.replace(microsecond=0) if value else value


def iso(value):
    return fields.Datetime.to_string(value) if value else False


def to_dt(day):
    """Date -> naive UTC datetime at midnight (None-safe)."""
    return fields.Datetime.to_datetime(day) if day else None


def col(key, label, ctype='text', align=None, width=None):
    return {'key': key, 'label': label, 'type': ctype,
            'align': align or ('end' if ctype in ('num', 'money', 'pct', 'days', 'hours', 'rate') else None),
            'width': width}


class DmagBoard(models.AbstractModel):
    _name = 'dmag.board'
    _description = 'DMAG Board engine'

    _dmag_key = None
    _dmag_title = ''
    _dmag_eyebrow = 'Dashboard điều hành · Định Vị Mới'
    _dmag_icon = 'fa-tachometer'
    _dmag_sequence = 50
    _dmag_group = 'dmag_dashboard.group_dmag_role_exec'
    _dmag_requires = ()
    _dmag_action = None

    # ------------------------------------------------------------------ registry
    @api.model
    def _boards(self):
        boards = [self.env[name] for name in self.env.registry if name.startswith('dmag.board.')]
        return sorted(boards, key=lambda b: b._dmag_sequence)

    @api.model
    def _board(self, key):
        name = f'dmag.board.{key}'
        if name not in self.env:
            raise AccessError(_("Bảng %s không tồn tại.", key))
        return self.env[name]

    @api.model
    def _available(self):
        return all(model in self.env for model in self._dmag_requires)

    @api.model
    def _allowed(self):
        return self.env.user.has_group(self._dmag_group)

    def _check_access(self):
        if not self._allowed():
            raise AccessError(_("Bạn chưa được cấp quyền xem bảng %s.", self._dmag_title))

    @api.model
    def get_menu(self):
        """Boards the current user may open (used by the board switcher)."""
        return [{'key': b._dmag_key, 'title': b._dmag_title, 'icon': b._dmag_icon,
                 'action': b._dmag_action, 'available': b._available()}
                for b in self._boards() if b._allowed()]

    # ------------------------------------------------------------------ metrics catalog
    @api.model
    def _dmag_metrics(self):
        return {}

    @api.model
    def _kpi_order(self):
        return []

    @api.model
    def _metric(self, D, key, start, end):
        raise NotImplementedError(key)

    @api.model
    def _all_metric_selection(self):
        res = []
        for board in self._boards():
            for key, meta in board._dmag_metrics().items():
                if meta.get('kind') != 'snapshot':
                    res.append((f'{board._dmag_key}.{key}', f'{board._dmag_title} · {meta["label"]}'))
        return res

    # ------------------------------------------------------------------ scope
    @api.model
    def _scope(self, params=None):
        params = params or {}
        period = max(7, min(int(params.get('period') or 30), 365))
        # periods are [start, end): +1s so whatever happened during the current second counts
        now = fields.Datetime.now() + timedelta(seconds=1)
        start = now - timedelta(days=period)
        company = self.env.company
        return {
            'period': period, 'now': now, 'start': start, 'prev_start': start - timedelta(days=period),
            'today': fields.Date.context_today(self),
            'filter_id': int(params.get('filter_id') or params.get('user_id') or 0),
            'company': company, 'company_ids': self.env.companies.ids,
            'currency': company.currency_id,
        }

    def _load(self, sc, since):
        return {'sc': sc, 'since': since}

    def _since(self, sc, rules):
        windows = [sc['now'] - timedelta(days=r.window_days * (r.baseline_periods + 1)) for r in rules]
        return min([sc['prev_start']] + windows)

    def _rules(self, sc):
        return self.env['dmag.alert.rule'].sudo().search([
            ('board', '=', self._dmag_key), ('company_id', 'in', sc['company_ids'] + [False])])

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _buckets(start, end):
        days = (end - start).days
        if days <= 31:
            n = days
        elif days <= 186:
            n = math.ceil(days / 7)
        else:
            n = 12
        step = (end - start) / n
        return [(start + step * i, start + step * (i + 1)) for i in range(n)]

    def _series(self, D, key, start, end):
        return [self._metric(D, key, s, e) for s, e in self._buckets(start, end)]

    def _delta(self, key, cur, prev):
        if cur is None or prev is None:
            return None
        if self._dmag_metrics()[key]['unit'] == 'percent':
            return round(cur - prev, 1)
        if not prev:
            return None if not cur else 100.0
        return round(100.0 * (cur - prev) / abs(prev), 1)

    def _tone(self, key, delta):
        good = self._dmag_metrics()[key].get('good')
        if not delta or not good:
            return 'neutral'
        return 'good' if (delta > 0) == (good == 'up') else 'bad'

    def _kpi(self, D, key):
        sc = D['sc']
        meta = self._dmag_metrics()[key]
        if meta.get('kind') == 'snapshot':
            cur, prev, spark = self._metric(D, key, sc['start'], sc['now']), None, []
        else:
            # flow metrics: this period vs the previous one; state metrics: value at both period ends
            cur = self._metric(D, key, sc['start'], sc['now'])
            prev = self._metric(D, key, sc['prev_start'], sc['start'])
            spark = self._series(D, key, sc['start'], sc['now'])
        delta = self._delta(key, cur, prev)
        return {
            'key': key, 'board': self._dmag_key, 'label': meta['label'], 'hint': meta.get('hint', ''),
            'tag': meta.get('tag', ''), 'accent': meta.get('accent', 'blue'), 'unit': meta['unit'],
            'value': cur, 'previous': prev, 'delta': delta, 'tone': self._tone(key, delta), 'spark': spark,
            'sub': self._kpi_sub(D, key) or '',
        }

    def _kpi_sub(self, D, key):
        return ''

    def _trend(self, D, groups, title='Nhịp vận hành'):
        sc = D['sc']
        metrics = self._dmag_metrics()
        series = {}
        for _label, keys in groups:
            for key in keys:
                series[key] = {
                    'label': metrics[key]['label'], 'unit': metrics[key]['unit'],
                    'kind': metrics[key].get('kind', 'flow'),
                    'current': self._series(D, key, sc['start'], sc['now']),
                    'previous': self._series(D, key, sc['prev_start'], sc['start']),
                }
        return {'type': 'trend', 'id': 'trend', 'title': title, 'series': series,
                'tabs': [{'group': g, 'keys': [k for k in keys]} for g, keys in groups],
                'default': groups[0][1][0]}

    def _filter(self, sc):
        """Optional filter shown in the header: {'label', 'options': [{id, name}]}."""
        return None

    def _custom_signals(self, D):
        return []

    def _signals(self, D, rules):
        out = []
        for rule in rules.filtered('active'):
            res = rule._evaluate(self, D)
            if res:
                out.append(res)
        out += self._custom_signals(D)
        for sig in out:
            sig.setdefault('board', self._dmag_key)
            sig.setdefault('board_title', self._dmag_title)
        rank = {'critical': 0, 'warning': 1, 'positive': 2, 'info': 3}
        out.sort(key=lambda s: rank.get(s['severity'], 9))
        return out

    def _users_options(self, users):
        return [{'id': u.id, 'name': u.name} for u in users.sorted('name')]

    def _money(self, amount, currency, date, sc):
        company = sc['company']
        if not currency or currency == company.currency_id:
            return amount
        return currency._convert(amount, company.currency_id, company, date or sc['today'])

    def _glossary(self):
        return [{'key': k, 'label': v['label'], 'hint': v.get('hint', '')}
                for k, v in self._dmag_metrics().items()]

    def _extra_glossary(self, D):
        return []

    def _rows(self, D):
        return []

    def _not_available(self):
        modules = ', '.join(self._dmag_requires)
        return {'type': 'empty', 'title': _('Chưa đủ dữ liệu nguồn'),
                'text': _('Bảng này cần các ứng dụng Odoo quản lý: %s. Hãy cài đặt chúng để bật bảng.', modules)}

    def _board_info(self):
        return {'key': self._dmag_key, 'title': self._dmag_title, 'eyebrow': self._dmag_eyebrow,
                'icon': self._dmag_icon}

    # ------------------------------------------------------------------ public API
    @api.model
    def get_board(self, params=None):
        self._check_access()
        sc = self._scope(params)
        base = {
            'board': self._board_info(), 'menu': self.get_menu(),
            'generated_at': iso(sc['now']), 'period': sc['period'],
            'currency': {'symbol': sc['currency'].symbol, 'position': sc['currency'].position},
            'filter': None, 'filter_id': sc['filter_id'],
        }
        if not self._available():
            return dict(base, kpis=[], signals=[], buckets=[], glossary=[],
                        rows=[{'widths': [12], 'panels': [self._not_available()]}])
        rules = self._rules(sc)
        D = self._load(sc, self._since(sc, rules))
        return dict(
            base,
            filter=self._filter(sc),
            kpis=[self._kpi(D, key) for key in self._kpi_order()],
            buckets=[iso(s) for s, _e in self._buckets(sc['start'], sc['now'])],
            signals=self._signals(D, rules),
            rows=self._rows(D),
            glossary=self._glossary() + self._extra_glossary(D),
        )

    @api.model
    def action_drill(self, spec, params=None):
        """Turn a click on the board into a native list/form action, filtered exactly like
        the number that was clicked."""
        if spec.get('board') and spec['board'] != self._dmag_key:
            board = self._board(spec['board'])
            board._check_access()
            return board._drill_dispatch(spec, params)
        self._check_access()
        return self._drill_dispatch(spec, params)

    def _drill_dispatch(self, spec, params):
        sc = self._scope(params)
        kind = spec.get('type')
        if kind == 'record':
            if spec['model'] not in DRILL_MODELS:
                raise AccessError(_("Model không được phép."))
            return {'type': 'ir.actions.act_window', 'res_model': spec['model'], 'res_id': int(spec['id']),
                    'views': [(False, 'form')], 'target': 'current'}
        if kind == 'ids':
            if spec['model'] not in DRILL_MODELS:
                raise AccessError(_("Model không được phép."))
            return self._list_action(spec['model'], [('id', 'in', [int(i) for i in spec.get('ids') or []])],
                                     spec.get('title') or '', spec.get('context') or {})
        res = self._drill(spec, sc)
        if not res:
            raise AccessError(_("Không xác định được dữ liệu chi tiết."))
        return res

    def _drill(self, spec, sc):
        return None

    def _list_action(self, model, domain, name, context=None, views=None):
        ctx = dict(context or {})
        return {'type': 'ir.actions.act_window', 'name': name, 'res_model': model, 'domain': domain,
                'views': views or [(False, 'list'), (False, 'form')], 'target': 'current', 'context': ctx}

    @api.model
    def get_detail(self, kind, res_id, params=None):
        self._check_access()
        sc = self._scope(params)
        return self._detail(kind, int(res_id), sc)

    def _detail(self, kind, res_id, sc):
        raise AccessError(_("Không có chi tiết cho mục này."))
