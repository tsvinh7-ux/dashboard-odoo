import bisect
import math
import statistics
from collections import defaultdict
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import AccessError
from odoo.addons.project.models.project_task import CLOSED_STATES

DONE = '1_done'

# Single source of truth for every number on the board: KPI cards, sparklines,
# trend chart, alert rules and their drill-downs all read from this catalog.
METRICS = {
    'contracts': {
        'label': 'Hợp đồng SPDV mới', 'group': 'sale', 'kind': 'flow', 'good': 'up', 'unit': 'count',
        'hint': 'Đơn bán có sản phẩm/dịch vụ SPDV được xác nhận trong kỳ.',
    },
    'enrollments': {
        'label': 'Học viên ghi danh', 'group': 'learn', 'kind': 'flow', 'good': 'up', 'unit': 'count',
        'hint': 'Lượt ghi danh khóa học mới trong kỳ (không tính lời mời chưa nhận, không tính nhân sự nội bộ).',
    },
    'lessons_done': {
        'label': 'Bài học hoàn thành', 'group': 'learn', 'kind': 'flow', 'good': 'up', 'unit': 'count',
        'hint': 'Số bài học được học viên hoàn thành trong kỳ — đo nhịp học thực tế.',
    },
    'course_done': {
        'label': 'Khóa học hoàn thành', 'group': 'learn', 'kind': 'flow', 'good': 'up', 'unit': 'count',
        'hint': 'Số học viên hoàn thành trọn khóa trong kỳ.',
    },
    'stalled': {
        'label': 'Học viên ngưng học', 'group': 'learn', 'kind': 'state', 'good': 'down', 'unit': 'count',
        'hint': 'Học viên chưa xong khóa và không hoàn thành bài nào trong N ngày gần nhất (tính tại cuối kỳ).',
    },
    'tasks_created': {
        'label': 'Đầu việc phát sinh', 'group': 'deploy', 'kind': 'flow', 'good': None, 'unit': 'count',
        'hint': 'Đầu việc triển khai mới được tạo trong kỳ.',
    },
    'tasks_done': {
        'label': 'Đầu việc triển khai xong', 'group': 'deploy', 'kind': 'flow', 'good': 'up', 'unit': 'count',
        'hint': 'Đầu việc thuộc dự án triển khai được đánh dấu Hoàn thành trong kỳ.',
    },
    'ontime_rate': {
        'label': 'Tỷ lệ đúng hạn', 'group': 'deploy', 'kind': 'ratio', 'good': 'up', 'unit': 'percent',
        'hint': 'Trong các đầu việc hoàn thành trong kỳ có deadline: % xong trước hoặc đúng deadline.',
    },
    'overdue': {
        'label': 'Đầu việc quá hạn', 'group': 'deploy', 'kind': 'state', 'good': 'down', 'unit': 'count',
        'hint': 'Đầu việc chưa đóng đã quá deadline (tính tại cuối kỳ).',
    },
}
KPI_ORDER = ['contracts', 'enrollments', 'lessons_done', 'stalled', 'ontime_rate', 'overdue']
TREND_ORDER = ['lessons_done', 'enrollments', 'course_done', 'stalled', 'tasks_done', 'tasks_created', 'overdue']
METRIC_SELECTION = [(k, v['label']) for k, v in METRICS.items()]

PROJECT_STATUS = {
    'on_track': ('Đúng tiến độ', 0), 'at_risk': ('Có rủi ro', 10), 'off_track': ('Chệch hướng', 20),
    'on_hold': ('Tạm dừng', 10), 'to_define': ('Chưa cập nhật', 0), 'done': ('Hoàn tất', 0),
}


def _dt(value):
    # ORM 'now' has no microseconds while create_date/write_date do: normalise so a record
    # created in the current second is not considered to be in the future.
    return value.replace(microsecond=0) if value else value


def _iso(dt):
    return fields.Datetime.to_string(dt) if dt else False


class DmagDashboard(models.AbstractModel):
    _name = 'dmag.dashboard'
    _description = 'DMAG Dashboard Engine'

    # ------------------------------------------------------------------
    # Scope & raw data
    # ------------------------------------------------------------------
    def _check_access(self):
        if not self.env.user.has_group('dmag_dashboard.group_dmag_dashboard_user'):
            raise AccessError(_("Bạn chưa được cấp quyền xem Dashboard điều hành DMAG."))

    @api.model
    def _scope(self, params=None):
        params = params or {}
        period = max(7, min(int(params.get('period') or 30), 365))
        # periods are [start, end): +1s so whatever happened during the current second counts
        now = fields.Datetime.now() + timedelta(seconds=1)
        start = now - timedelta(days=period)
        company = self.env.company
        return {
            'period': period,
            'now': now,
            'start': start,
            'prev_start': start - timedelta(days=period),
            'user_id': int(params.get('user_id') or 0),
            'company': company,
            'company_ids': self.env.companies.ids,
            'stall': timedelta(days=company.dmag_stall_days or 14),
            'idle': timedelta(days=company.dmag_idle_days or 21),
        }

    def _so_domain(self, sc):
        dom = [('state', '=', 'sale'), ('company_id', 'in', sc['company_ids'])]
        categs = sc['company'].dmag_spdv_categ_ids
        if categs:
            categ_ids = self.env['product.category'].sudo().search([('id', 'child_of', categs.ids)]).ids
            dom.append(('order_line.product_id.categ_id', 'in', categ_ids))
        else:
            dom.append(('order_line.product_id.type', '=', 'service'))
        if sc['user_id']:
            dom.append(('user_id', '=', sc['user_id']))
        return dom

    def _project_domain(self, sc):
        dom = [('company_id', 'in', sc['company_ids'] + [False])]
        mode = sc['company'].dmag_project_scope
        if mode == 'customer':
            dom.append(('partner_id', '!=', False))
        elif mode == 'sale':
            dom.append(('sale_line_id', '!=', False))
        return dom

    def _task_domain(self, sc):
        dom = [('project_id', '!=', False), ('company_id', 'in', sc['company_ids'] + [False])]
        mode = sc['company'].dmag_project_scope
        if mode == 'customer':
            dom.append(('project_id.partner_id', '!=', False))
        elif mode == 'sale':
            dom.append(('project_id.sale_line_id', '!=', False))
        if sc['user_id']:
            dom.append(('user_ids', 'in', [sc['user_id']]))
        return dom

    def _enroll_domain(self, sc):
        dom = [('member_status', '!=', 'invited'),
               ('partner_id.commercial_partner_id', '!=', sc['company'].partner_id.id)]
        if sc['user_id']:
            dom.append(('channel_id.user_id', '=', sc['user_id']))
        return dom

    def _lesson_domain(self, sc):
        dom = [('completed', '=', True),
               ('partner_id.commercial_partner_id', '!=', sc['company'].partner_id.id)]
        if sc['user_id']:
            dom.append(('channel_id.user_id', '=', sc['user_id']))
        return dom

    @api.model
    def _load(self, sc, since):
        """Read every raw row the board needs once; all metrics are then pure Python."""
        env = self.env
        D = {'sc': sc, 'since': since}

        orders = env['sale.order'].sudo().search(self._so_domain(sc) + [('date_order', '>=', since)])
        D['orders'] = [{
            'id': o.id, 'date': _dt(o.date_order), 'amount': o.currency_id._convert(
                o.amount_untaxed, sc['company'].currency_id, sc['company'], o.date_order.date())
            if o.currency_id != sc['company'].currency_id else o.amount_untaxed,
            'customer': o.partner_id.commercial_partner_id.id,
        } for o in orders]

        enrolls = env['slide.channel.partner'].sudo().search(self._enroll_domain(sc))
        D['enrolls'] = [{
            'id': e.id, 'partner': e.partner_id.id, 'customer': e.partner_id.commercial_partner_id.id,
            'channel': e.channel_id.id, 'create': _dt(e.create_date), 'done': _dt(e.dmag_completed_date),
            'status': e.member_status, 'completion': e.completion,
        } for e in enrolls]

        Lesson = env['slide.slide.partner'].sudo()
        lessons = Lesson.search_read(
            self._lesson_domain(sc) + [('write_date', '>=', since - sc['stall'])],
            ['partner_id', 'channel_id', 'write_date'])
        act = defaultdict(list)
        D['lessons'] = []
        for row in lessons:
            key = (row['partner_id'] and row['partner_id'][0], row['channel_id'] and row['channel_id'][0])
            act[key].append(_dt(row['write_date']))
            D['lessons'].append({'key': key, 'date': _dt(row['write_date'])})
        for dates in act.values():
            dates.sort()
        D['act'] = act
        D['last_act'] = {
            (partner.id, channel.id): _dt(last)
            for partner, channel, last in Lesson._read_group(
                self._lesson_domain(sc) + [('partner_id', 'in', list({e['partner'] for e in D['enrolls']}))],
                ['partner_id', 'channel_id'], ['write_date:max'])
        }

        Task = env['project.task'].sudo()
        tasks = Task.search(self._task_domain(sc) + [
            '|', ('dmag_closed_date', '=', False), ('dmag_closed_date', '>=', since)])
        D['tasks'] = [{
            'id': t.id, 'create': _dt(t.create_date), 'deadline': t.date_deadline, 'closed': _dt(t.dmag_closed_date),
            'state': t.state, 'project': t.project_id.id,
            'customer': (t.project_id.partner_id or t.partner_id).commercial_partner_id.id,
            'users': t.user_ids.ids,
        } for t in tasks]
        return D

    # ------------------------------------------------------------------
    # Metric engine
    # ------------------------------------------------------------------
    def _stalled_ids(self, D, t):
        cut = t - D['sc']['stall']
        res = []
        for e in D['enrolls']:
            if e['create'] > cut or (e['done'] and e['done'] <= t):
                continue
            if e['status'] == 'completed' and not e['done']:
                continue
            acts = D['act'].get((e['partner'], e['channel']), [])
            i = bisect.bisect_right(acts, cut)
            if i < len(acts) and acts[i] <= t:
                continue
            res.append(e['id'])
        return res

    def _overdue_rows(self, D, t):
        return [x for x in D['tasks'] if x['deadline'] and x['deadline'] < t and x['create'] <= t
                and (not x['closed'] or x['closed'] > t)]

    def _metric(self, D, key, start, end):
        if key == 'contracts':
            return sum(1 for o in D['orders'] if start <= o['date'] < end)
        if key == 'enrollments':
            return sum(1 for e in D['enrolls'] if start <= e['create'] < end)
        if key == 'lessons_done':
            return sum(1 for x in D['lessons'] if start <= x['date'] < end)
        if key == 'course_done':
            return sum(1 for e in D['enrolls'] if e['done'] and start <= e['done'] < end)
        if key == 'stalled':
            return len(self._stalled_ids(D, end))
        if key == 'tasks_created':
            return sum(1 for x in D['tasks'] if start <= x['create'] < end)
        if key == 'tasks_done':
            return sum(1 for x in D['tasks'] if x['state'] == DONE and x['closed'] and start <= x['closed'] < end)
        if key == 'ontime_rate':
            rows = [x for x in D['tasks'] if x['state'] == DONE and x['closed'] and x['deadline']
                    and start <= x['closed'] < end]
            if not rows:
                return None
            return round(100.0 * sum(1 for x in rows if x['closed'] <= x['deadline']) / len(rows), 1)
        if key == 'overdue':
            return len(self._overdue_rows(D, end))
        raise ValueError(key)

    def _buckets(self, start, end):
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

    @staticmethod
    def _delta(key, cur, prev):
        if cur is None or prev is None:
            return None
        if METRICS[key]['unit'] == 'percent':
            return round(cur - prev, 1)
        if not prev:
            return None if not cur else 100.0
        return round(100.0 * (cur - prev) / prev, 1)

    @staticmethod
    def _tone(key, delta):
        good = METRICS[key]['good']
        if not delta or not good:
            return 'neutral'
        return 'good' if (delta > 0) == (good == 'up') else 'bad'

    # ------------------------------------------------------------------
    # Public API (called by the OWL client action)
    # ------------------------------------------------------------------
    @api.model
    def get_dashboard_data(self, params=None):
        self._check_access()
        sc = self._scope(params)
        rules = self.env['dmag.alert.rule'].sudo().search([('company_id', 'in', sc['company_ids'] + [False])])
        since = min([sc['prev_start']] + [sc['now'] - timedelta(days=r.window_days * (r.baseline_periods + 1))
                                          for r in rules])
        D = self._load(sc, since)
        now, start, prev_start = sc['now'], sc['start'], sc['prev_start']

        kpis = []
        for key in KPI_ORDER:
            meta = METRICS[key]
            # flow metrics: this period vs previous one; state metrics: value at both period ends
            cur, prev = self._metric(D, key, start, now), self._metric(D, key, prev_start, start)
            delta = self._delta(key, cur, prev)
            kpis.append(dict(meta, key=key, value=cur, previous=prev, delta=delta,
                             tone=self._tone(key, delta), spark=self._series(D, key, start, now),
                             sub=self._kpi_sub(D, key)))

        buckets = self._buckets(start, now)
        trends = {key: {
            'label': METRICS[key]['label'], 'good': METRICS[key]['good'],
            'current': self._series(D, key, start, now),
            'previous': self._series(D, key, prev_start, start),
        } for key in TREND_ORDER}

        customers = self._customer_rows(D)
        return {
            'generated_at': _iso(now),
            'period': sc['period'],
            'range': {'start': _iso(start), 'end': _iso(now)},
            'currency': {'symbol': sc['company'].currency_id.symbol,
                         'position': sc['company'].currency_id.position},
            'settings': {'stall_days': sc['stall'].days, 'idle_days': sc['idle'].days},
            'kpis': kpis,
            'buckets': [_iso(s) for s, _e in buckets],
            'trends': trends,
            'funnel': self._funnel(D),
            'customers': customers[:60],
            'health_summary': self._health_summary(customers),
            'courses': self._course_rows(D),
            'team': self._team_rows(D),
            'signals': self._signals(D, rules, customers),
            'users': [{'id': u.id, 'name': u.name} for u in self.env['res.users'].search(
                [('share', '=', False)], order='name', limit=300)],
            'glossary': [{'key': k, 'label': v['label'], 'hint': v['hint']} for k, v in METRICS.items()],
        }

    def _kpi_sub(self, D, key):
        sc = D['sc']
        start, now = sc['start'], sc['now']
        if key == 'contracts':
            return {'amount': sum(o['amount'] for o in D['orders'] if start <= o['date'] < now)}
        if key == 'enrollments':
            return {'text': _('%s khóa được hoàn thành', self._metric(D, 'course_done', start, now))}
        if key == 'lessons_done':
            learners = {x['key'][0] for x in D['lessons'] if start <= x['date'] < now}
            return {'text': _('từ %s học viên tích cực', len(learners))}
        if key == 'stalled':
            active = sum(1 for e in D['enrolls'] if e['status'] in ('joined', 'ongoing'))
            return {'text': _('trên %s học viên đang học', active)}
        if key == 'ontime_rate':
            rows = [x for x in D['tasks'] if x['state'] == DONE and x['closed'] and x['deadline']
                    and start <= x['closed'] < now]
            ok = sum(1 for x in rows if x['closed'] <= x['deadline'])
            return {'text': _('%s/%s đầu việc có deadline', ok, len(rows))}
        if key == 'overdue':
            rows = self._overdue_rows(D, now)
            worst = max((max(1, (now - x['deadline']).days) for x in rows), default=0)
            return {'text': _('trễ nhất %s ngày', worst) if rows else _('Không có đầu việc trễ')}
        return {}

    # -- customer journey -------------------------------------------------
    def _projects_by_customer(self, D, customer_ids=None):
        sc = D['sc']
        dom = self._project_domain(sc)
        if customer_ids is not None:
            dom.append(('partner_id.commercial_partner_id', 'in', list(customer_ids)))
        projects = self.env['project.project'].sudo().search(dom)
        counts = defaultdict(lambda: {'total': 0, 'closed': 0, 'done': 0})
        for project, state, count in self.env['project.task'].sudo()._read_group(
                [('project_id', 'in', projects.ids)], ['project_id', 'state'], ['__count']):
            c = counts[project.id]
            if state != '1_canceled':
                c['total'] += count
            if state == DONE:
                c['done'] += count
            if state in CLOSED_STATES:
                c['closed'] += count
        late = defaultdict(int)
        today = fields.Date.context_today(self)
        for project, count in self.env['project.milestone'].sudo()._read_group(
                [('project_id', 'in', projects.ids), ('is_reached', '=', False), ('deadline', '<', today)],
                ['project_id'], ['__count']):
            late[project.id] = count
        res = defaultdict(list)
        for p in projects:
            cust = p.partner_id.commercial_partner_id.id
            if cust:
                res[cust].append({'project': p, 'counts': counts[p.id], 'late_milestones': late[p.id]})
        return res

    def _funnel(self, D):
        sc = D['sc']
        cohort = {o['customer'] for o in D['orders'] if sc['start'] <= o['date'] < sc['now']}
        projects = self._projects_by_customer(D, cohort)
        enrolls = defaultdict(list)
        for e in D['enrolls']:
            if e['customer'] in cohort:
                enrolls[e['customer']].append(e)
        stages = [
            ('signed', 'Ký hợp đồng', lambda c: True),
            ('kickoff', 'Kick-off dự án', lambda c: bool(projects.get(c))),
            ('learning', 'Bắt đầu học', lambda c: any(
                e['completion'] > 0 or e['status'] in ('ongoing', 'completed') for e in enrolls[c])),
            ('learned', 'Học xong chương trình', lambda c: bool(enrolls[c]) and all(
                e['status'] == 'completed' for e in enrolls[c])),
            ('deploying', 'Đang triển khai', lambda c: any(p['counts']['done'] for p in projects.get(c, []))),
            ('accepted', 'Nghiệm thu', lambda c: bool(projects.get(c)) and all(
                p['project'].last_update_status == 'done'
                or (p['counts']['total'] and p['counts']['closed'] >= p['counts']['total'])
                for p in projects[c])),
        ]
        total = len(cohort) or 1
        out = []
        for key, label, test in stages:
            ids = [c for c in cohort if test(c)]
            out.append({'key': key, 'label': label, 'count': len(ids), 'ids': ids,
                        'pct': round(100.0 * len(ids) / total, 1) if cohort else 0})
        return {'stages': out, 'speed': self._speed(D, cohort, projects)}

    def _speed(self, D, cohort, projects):
        """Time-to-value of the cohort: how fast a signed customer is kicked off and starts learning."""
        sc = D['sc']
        signed = {}
        for o in D['orders']:
            if o['customer'] in cohort:
                signed[o['customer']] = min(signed.get(o['customer'], o['date']), o['date'])
        kickoff = []
        for c, plist in projects.items():
            starts = [fields.Datetime.to_datetime(p['project'].date_start) if p['project'].date_start
                      else p['project'].create_date for p in plist]
            if c in signed and starts:
                kickoff.append(max(0, (min(starts) - signed[c]).days))
        first_lesson = defaultdict(lambda: False)
        partners = self.env['res.partner'].sudo()
        for partner, first in self.env['slide.slide.partner'].sudo()._read_group(
                self._lesson_domain(sc) + [('partner_id.commercial_partner_id', 'in', list(cohort))],
                ['partner_id'], ['write_date:min']):
            c = partner.commercial_partner_id.id
            first_lesson[c] = min(first_lesson[c] or first, first)
        learn = [max(0, (first_lesson[c] - signed[c]).days) for c in signed if first_lesson[c]]
        waiting = [c for c in cohort if not projects.get(c) and sc['now'] - signed[c] > timedelta(days=7)]
        return {
            'kickoff_days': round(sum(kickoff) / len(kickoff), 1) if kickoff else None,
            'first_lesson_days': round(sum(learn) / len(learn), 1) if learn else None,
            'waiting_kickoff': waiting,
        }

    def _customer_rows(self, D, only=None):
        sc = D['sc']
        now = sc['now']
        stalled = set(self._stalled_ids(D, now))
        by_c = defaultdict(lambda: {'enrolls': [], 'open': [], 'overdue': [], 'closed_dates': []})
        for e in D['enrolls']:
            by_c[e['customer']]['enrolls'].append(e)
        for x in D['tasks']:
            if not x['customer']:
                continue
            row = by_c[x['customer']]
            if x['state'] in CLOSED_STATES:
                if x['closed']:
                    row['closed_dates'].append(x['closed'])
            else:
                row['open'].append(x)
                if x['deadline'] and x['deadline'] < now:
                    row['overdue'].append(x)
        active = {c for c, r in by_c.items() if c and (
            r['open'] or any(e['status'] in ('joined', 'ongoing') for e in r['enrolls']))}
        if only is not None:
            active &= set(only)
        projects = self._projects_by_customer(D, active)
        partners = self.env['res.partner'].sudo().browse(list(active))
        rows = []
        for partner in partners:
            c = partner.id
            r = by_c[c]
            enr = r['enrolls']
            n_stalled = sum(1 for e in enr if e['id'] in stalled)
            learn_pct = round(sum(e['completion'] for e in enr) / len(enr)) if enr else None
            plist = projects.get(c, [])
            total = sum(p['counts']['total'] for p in plist)
            closed = sum(p['counts']['closed'] for p in plist)
            late_ms = sum(p['late_milestones'] for p in plist)
            worst_status = max((p['project'].last_update_status for p in plist),
                               key=lambda s: PROJECT_STATUS.get(s, ('', 0))[1], default=False)
            last = max([d for d in r['closed_dates']]
                       + [D['last_act'].get((e['partner'], e['channel'])) or e['create'] for e in enr]
                       + [x['create'] for x in r['open']], default=False)
            max_late = max((max(1, (now - x['deadline']).days) for x in r['overdue']), default=0)

            score, reasons = 100, []
            if r['overdue']:
                score -= min(36, 6 * len(r['overdue']))
                reasons.append({'tone': 'bad', 'text': _('%s đầu việc quá hạn (trễ nhất %s ngày)',
                                                         len(r['overdue']), max_late)})
            if n_stalled:
                score -= min(32, 8 * n_stalled)
                reasons.append({'tone': 'bad', 'text': _('%s học viên ngưng học', n_stalled)})
            if late_ms:
                score -= min(20, 10 * late_ms)
                reasons.append({'tone': 'bad', 'text': _('%s mốc nghiệm thu trễ', late_ms)})
            if worst_status and PROJECT_STATUS.get(worst_status, ('', 0))[1]:
                score -= PROJECT_STATUS[worst_status][1]
                reasons.append({'tone': 'warn', 'text': _('Dự án báo: %s', PROJECT_STATUS[worst_status][0])})
            if last and now - last > sc['idle']:
                score -= 15
                reasons.append({'tone': 'warn', 'text': _('Không hoạt động %s ngày', (now - last).days)})
            if not plist and enr:
                reasons.append({'tone': 'info', 'text': _('Chưa kick-off dự án')})
            score = max(0, score)
            rows.append({
                'id': c, 'name': partner.display_name,
                'salesperson': partner.user_id.name or '',
                'score': score, 'level': 'good' if score >= 75 else 'watch' if score >= 50 else 'risk',
                'learners': len({e['partner'] for e in enr}), 'learn_pct': learn_pct, 'stalled': n_stalled,
                'tasks_total': total, 'tasks_closed': closed,
                'deploy_pct': round(100.0 * closed / total) if total else None,
                'overdue': len(r['overdue']), 'max_late': max_late, 'late_milestones': late_ms,
                'projects': len(plist), 'last_activity': _iso(last),
                'idle_days': (now - last).days if last else None,
                'reasons': reasons,
            })
        rows.sort(key=lambda x: (x['score'], -x['overdue'], -x['stalled'], x['name']))
        return rows

    @staticmethod
    def _health_summary(customers):
        res = {'good': 0, 'watch': 0, 'risk': 0, 'total': len(customers)}
        for c in customers:
            res[c['level']] += 1
        return res

    def _course_rows(self, D):
        sc = D['sc']
        start, now = sc['start'], sc['now']
        stalled = set(self._stalled_ids(D, now))
        by = defaultdict(list)
        for e in D['enrolls']:
            by[e['channel']].append(e)
        lessons = defaultdict(int)
        for x in D['lessons']:
            if start <= x['date'] < now:
                lessons[x['key'][1]] += 1
        rows = []
        for channel in self.env['slide.channel'].sudo().browse(list(by)):
            enr = by[channel.id]
            rows.append({
                'id': channel.id, 'name': channel.name, 'responsible': channel.user_id.name or '',
                'enrolled': len(enr),
                'active': sum(1 for e in enr if e['status'] in ('joined', 'ongoing')),
                'completed': sum(1 for e in enr if e['status'] == 'completed'),
                'avg_completion': round(sum(e['completion'] for e in enr) / len(enr)) if enr else 0,
                'stalled': sum(1 for e in enr if e['id'] in stalled),
                'new': sum(1 for e in enr if start <= e['create'] < now),
                'lessons': lessons[channel.id],
            })
        rows.sort(key=lambda r: (-r['stalled'], -r['active'], r['name']))
        return rows

    def _team_rows(self, D):
        sc = D['sc']
        start, now = sc['start'], sc['now']
        stats = defaultdict(lambda: {'open': 0, 'overdue': 0, 'done': 0, 'ontime': 0, 'with_deadline': 0})
        for x in D['tasks']:
            for uid in (x['users'] or [0]):
                s = stats[uid]
                if x['state'] not in CLOSED_STATES:
                    s['open'] += 1
                    if x['deadline'] and x['deadline'] < now:
                        s['overdue'] += 1
                elif x['state'] == DONE and x['closed'] and start <= x['closed'] < now:
                    s['done'] += 1
                    if x['deadline']:
                        s['with_deadline'] += 1
                        s['ontime'] += x['closed'] <= x['deadline']
        users = {u.id: u.name for u in self.env['res.users'].sudo().browse([u for u in stats if u])}
        rows = []
        for uid, s in stats.items():
            if not (s['open'] or s['done']):
                continue
            rows.append(dict(s, id=uid, name=users.get(uid) or _('Chưa giao việc'),
                             ontime_rate=round(100.0 * s['ontime'] / s['with_deadline'])
                             if s['with_deadline'] else None))
        rows.sort(key=lambda r: (-r['overdue'], -r['open'], r['name']))
        return rows

    # -- insights ---------------------------------------------------------
    def _signals(self, D, rules, customers):
        out = []
        for rule in rules.filtered('active'):
            res = rule._evaluate(self, D)
            if res:
                out.append(res)
        risk = [c for c in customers if c['level'] == 'risk']
        if risk:
            out.append({
                'severity': 'critical', 'metric': 'health', 'title': _('Khách hàng nguy cơ'),
                'message': _('%s khách hàng đang ở mức Nguy cơ — cao nhất: %s (%s điểm).',
                             len(risk), risk[0]['name'], risk[0]['score']),
                'customer_id': risk[0]['id'],
            })
        unassigned = [x for x in D['tasks'] if not x['users'] and x['state'] not in CLOSED_STATES]
        if unassigned:
            out.append({
                'severity': 'warning', 'metric': 'unassigned', 'title': _('Đầu việc chưa giao'),
                'message': _('%s đầu việc triển khai đang mở chưa có người phụ trách.', len(unassigned)),
                'ids': [x['id'] for x in unassigned],
            })
        rank = {'critical': 0, 'warning': 1, 'positive': 2, 'info': 3}
        out.sort(key=lambda s: rank.get(s['severity'], 9))
        return out

    # -- detail drawers ------------------------------------------------------
    @api.model
    def get_customer_detail(self, partner_id, params=None):
        self._check_access()
        sc = self._scope(params)
        D = self._load(sc, sc['prev_start'])
        partner = self.env['res.partner'].sudo().browse(int(partner_id)).commercial_partner_id
        now = sc['now']
        rows = self._customer_rows(D, only={partner.id})
        stalled = set(self._stalled_ids(D, now))
        channels = {c.id: c.name for c in self.env['slide.channel'].sudo().browse(
            list({e['channel'] for e in D['enrolls'] if e['customer'] == partner.id}))}
        learners = []
        for e in D['enrolls']:
            if e['customer'] != partner.id:
                continue
            last = D['last_act'].get((e['partner'], e['channel']))
            learners.append({
                'id': e['id'], 'name': self.env['res.partner'].sudo().browse(e['partner']).name,
                'course': channels.get(e['channel'], ''), 'completion': e['completion'], 'status': e['status'],
                'stalled': e['id'] in stalled, 'last_activity': _iso(last),
                'idle_days': (now - (last or e['create'])).days,
            })
        learners.sort(key=lambda r: (not r['stalled'], r['status'] == 'completed', r['completion']))

        projects = []
        for p in self._projects_by_customer(D, {partner.id}).get(partner.id, []):
            proj = p['project']
            nxt = proj.milestone_ids.filtered(lambda m: not m.is_reached and m.deadline).sorted('deadline')[:1]
            projects.append({
                'id': proj.id, 'name': proj.name, 'manager': proj.user_id.name or '',
                'status': proj.last_update_status, 'status_label': PROJECT_STATUS.get(
                    proj.last_update_status, ('', 0))[0],
                'total': p['counts']['total'], 'closed': p['counts']['closed'],
                'pct': round(100.0 * p['counts']['closed'] / p['counts']['total']) if p['counts']['total'] else 0,
                'late_milestones': p['late_milestones'],
                'next_milestone': nxt and {'name': nxt.name, 'deadline': fields.Date.to_string(nxt.deadline),
                                           'late': nxt.deadline < now.date()},
            })
        tasks = self.env['project.task'].sudo().search(
            self._task_domain(dict(sc, user_id=0)) + [
                ('project_id', 'in', [p['id'] for p in projects]),
                ('state', 'not in', list(CLOSED_STATES))],
            order='date_deadline asc', limit=20)
        open_tasks = [{
            'id': t.id, 'name': t.name, 'project': t.project_id.name, 'stage': t.stage_id.name or '',
            'users': ', '.join(t.user_ids.mapped('name')), 'deadline': _iso(t.date_deadline),
            'late_days': max(1, (now - t.date_deadline).days) if t.date_deadline and t.date_deadline < now else 0,
        } for t in tasks]
        orders = self.env['sale.order'].sudo().search(
            self._so_domain(dict(sc, user_id=0)) + [('partner_id', 'child_of', partner.id)],
            order='date_order desc', limit=10)
        return {
            'id': partner.id, 'name': partner.display_name, 'email': partner.email or '',
            'phone': partner.phone or partner.mobile or '', 'salesperson': partner.user_id.name or '',
            'health': rows[0] if rows else None,
            'learners': learners, 'projects': projects, 'tasks': open_tasks,
            'orders': [{'id': o.id, 'name': o.name, 'date': _iso(o.date_order), 'amount': o.amount_untaxed,
                        'invoice_status': o.invoice_status} for o in orders],
        }

    @api.model
    def get_course_detail(self, channel_id, params=None):
        self._check_access()
        sc = self._scope(params)
        D = self._load(sc, sc['prev_start'])
        now = sc['now']
        channel = self.env['slide.channel'].sudo().browse(int(channel_id))
        stalled = set(self._stalled_ids(D, now))
        partners = self.env['res.partner'].sudo()
        learners = []
        for e in D['enrolls']:
            if e['channel'] != channel.id:
                continue
            p = partners.browse(e['partner'])
            last = D['last_act'].get((e['partner'], e['channel']))
            learners.append({
                'id': e['id'], 'name': p.name, 'customer': p.commercial_partner_id.name,
                'customer_id': p.commercial_partner_id.id, 'completion': e['completion'], 'status': e['status'],
                'stalled': e['id'] in stalled, 'last_activity': _iso(last),
                'idle_days': (now - (last or e['create'])).days,
            })
        learners.sort(key=lambda r: (not r['stalled'], r['status'] == 'completed', r['completion']))
        return {'id': channel.id, 'name': channel.name, 'responsible': channel.user_id.name or '',
                'total_slides': channel.total_slides, 'learners': learners}

    # -- drill-down into native Odoo views ------------------------------------
    @api.model
    def action_drill(self, spec, params=None):
        """Turn a click on the board into a native list/form action, filtered exactly
        like the number the user clicked on. Native views keep the user's own ACLs."""
        self._check_access()
        sc = self._scope(params)
        kind = spec.get('type')
        start, now = fields.Datetime.to_string(sc['start']), fields.Datetime.to_string(sc['now'])
        if kind == 'record':
            model = spec['model']
            if model not in ('res.partner', 'sale.order', 'project.task', 'project.project',
                             'slide.channel', 'slide.channel.partner'):
                raise AccessError(_("Model không được phép."))
            return {'type': 'ir.actions.act_window', 'res_model': model, 'res_id': int(spec['id']),
                    'views': [(False, 'form')], 'target': 'current'}

        model, domain, name, ctx = None, [], spec.get('title') or '', {}
        if kind == 'ids':
            model = spec['model']
            if model not in ('res.partner', 'project.task', 'slide.channel.partner', 'sale.order'):
                raise AccessError(_("Model không được phép."))
            domain = [('id', 'in', [int(i) for i in spec.get('ids') or []])]
        elif kind == 'kpi':
            key = spec['key']
            name = name or METRICS[key]['label']
            if key == 'contracts':
                model, domain = 'sale.order', self._so_domain(sc) + [('date_order', '>=', start)]
            elif key == 'enrollments':
                model, domain = 'slide.channel.partner', self._enroll_domain(sc) + [('create_date', '>=', start)]
            elif key == 'course_done':
                model, domain = 'slide.channel.partner', self._enroll_domain(sc) + [
                    ('dmag_completed_date', '>=', start)]
            elif key == 'lessons_done':
                model, domain = 'slide.channel.partner', self._enroll_domain(sc) + [
                    ('member_status', 'in', ('ongoing', 'completed')), ('write_date', '>=', start)]
                name = _('Học viên có tiến độ trong kỳ')
            elif key == 'stalled':
                D = self._load(sc, sc['start'])
                model, domain = 'slide.channel.partner', [('id', 'in', self._stalled_ids(D, sc['now']))]
            elif key in ('tasks_done', 'ontime_rate'):
                model = 'project.task'
                domain = self._task_domain(sc) + [('state', '=', DONE), ('dmag_closed_date', '>=', start)]
                if key == 'ontime_rate':
                    domain.append(('date_deadline', '!=', False))
                    name = _('Đầu việc hoàn thành có deadline')
            elif key == 'tasks_created':
                model, domain = 'project.task', self._task_domain(sc) + [('create_date', '>=', start)]
            elif key == 'overdue':
                model, domain = 'project.task', self._task_domain(sc) + [
                    ('state', 'not in', list(CLOSED_STATES)), ('date_deadline', '<', now)]
        elif kind == 'course':
            model, domain = 'slide.channel.partner', self._enroll_domain(sc) + [
                ('channel_id', '=', int(spec['id']))]
        elif kind == 'user':
            uid = int(spec['id'])
            model = 'project.task'
            domain = self._task_domain(dict(sc, user_id=0)) + [('state', 'not in', list(CLOSED_STATES))]
            domain.append(('user_ids', 'in', [uid]) if uid else ('user_ids', '=', False))
        elif kind == 'project':
            model, domain = 'project.task', [('project_id', '=', int(spec['id']))]
            ctx = {'search_default_open_tasks': 1}
        if not model:
            raise AccessError(_("Không xác định được dữ liệu chi tiết."))
        views = [(False, 'list'), (False, 'form')]
        if model == 'project.task':
            views = [(self.env.ref('project.view_task_tree2').id, 'list'), (False, 'kanban'), (False, 'form')]
            if kind != 'project':
                ctx = {'group_by': ['project_id']}
        elif model == 'slide.channel.partner' and kind != 'course':
            ctx = {'group_by': ['channel_id']}
        return {'type': 'ir.actions.act_window', 'name': name, 'res_model': model, 'domain': domain,
                'views': views, 'target': 'current', 'context': ctx}
