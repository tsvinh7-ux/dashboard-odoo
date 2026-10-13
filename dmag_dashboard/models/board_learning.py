import bisect
from collections import defaultdict
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.addons.project.models.project_task import CLOSED_STATES

from .board_base import col, dt, iso

DONE = '1_done'
PROJECT_STATUS = {
    'on_track': ('Đúng tiến độ', 0, 'good'), 'at_risk': ('Có rủi ro', 10, 'warn'),
    'off_track': ('Chệch hướng', 20, 'bad'), 'on_hold': ('Tạm dừng', 10, 'warn'),
    'to_define': ('Chưa cập nhật', 0, None), 'done': ('Hoàn tất', 0, 'good'),
}
ENROLL_STATUS = {'joined': 'Mới ghi danh', 'ongoing': 'Đang học', 'completed': 'Hoàn thành', 'invited': 'Được mời'}


def ago(days):
    if days is None:
        return '—'
    if days <= 0:
        return 'hôm nay'
    if days == 1:
        return 'hôm qua'
    return f'{days} ngày trước' if days < 30 else f'{round(days / 30)} tháng trước'


class DmagBoardLearning(models.AbstractModel):
    _name = 'dmag.board.learning'
    _inherit = 'dmag.board'
    _description = 'DMAG Board · Học & Triển khai SPDV'

    _dmag_key = 'learning'
    _dmag_title = 'Học & Triển khai SPDV'
    _dmag_icon = 'fa-graduation-cap'
    _dmag_sequence = 40
    _dmag_group = 'dmag_dashboard.group_dmag_role_delivery'
    _dmag_requires = ('slide.channel.partner', 'project.task', 'sale.order')
    _dmag_action = 'dmag_dashboard.action_dmag_learning_deploy'

    @api.model
    def _dmag_metrics(self):
        return {
            'contracts': {'label': 'Hợp đồng SPDV mới', 'tag': 'Bán', 'accent': 'navy', 'unit': 'count',
                          'good': 'up', 'hint': 'Đơn bán có sản phẩm/dịch vụ SPDV được xác nhận trong kỳ.'},
            'enrollments': {'label': 'Học viên ghi danh', 'tag': 'Học', 'unit': 'count', 'good': 'up',
                            'hint': 'Lượt ghi danh khóa học mới trong kỳ (không tính lời mời chưa nhận, '
                                    'không tính nhân sự nội bộ).'},
            'lessons_done': {'label': 'Bài học hoàn thành', 'tag': 'Học', 'unit': 'count', 'good': 'up',
                             'hint': 'Số bài học được học viên hoàn thành trong kỳ — đo nhịp học thực tế.'},
            'course_done': {'label': 'Khóa học hoàn thành', 'tag': 'Học', 'unit': 'count', 'good': 'up',
                            'hint': 'Số học viên hoàn thành trọn khóa trong kỳ.'},
            'stalled': {'label': 'Học viên ngưng học', 'tag': 'Học', 'kind': 'state', 'unit': 'count',
                        'good': 'down', 'hint': 'Học viên chưa xong khóa và không hoàn thành bài nào trong N ngày '
                                                'gần nhất (tính tại cuối kỳ).'},
            'tasks_created': {'label': 'Đầu việc phát sinh', 'tag': 'Triển khai', 'accent': 'cyan',
                              'unit': 'count', 'good': None, 'hint': 'Đầu việc triển khai mới được tạo trong kỳ.'},
            'tasks_done': {'label': 'Đầu việc triển khai xong', 'tag': 'Triển khai', 'accent': 'cyan',
                           'unit': 'count', 'good': 'up',
                           'hint': 'Đầu việc thuộc dự án triển khai được đánh dấu Hoàn thành trong kỳ.'},
            'ontime_rate': {'label': 'Tỷ lệ đúng hạn', 'tag': 'Triển khai', 'accent': 'cyan', 'kind': 'ratio',
                            'unit': 'percent', 'good': 'up',
                            'hint': 'Trong các đầu việc hoàn thành trong kỳ có deadline: % xong trước/đúng deadline.'},
            'overdue': {'label': 'Đầu việc quá hạn', 'tag': 'Triển khai', 'accent': 'cyan', 'kind': 'state',
                        'unit': 'count', 'good': 'down',
                        'hint': 'Đầu việc chưa đóng đã quá deadline (tính tại cuối kỳ).'},
        }

    @api.model
    def _kpi_order(self):
        return ['contracts', 'enrollments', 'lessons_done', 'stalled', 'ontime_rate', 'overdue']

    # ------------------------------------------------------------------ scope / data
    def _scope(self, params=None):
        sc = super()._scope(params)
        company = sc['company']
        sc['stall'] = timedelta(days=company.dmag_stall_days or 14)
        sc['idle'] = timedelta(days=company.dmag_idle_days or 21)
        sc['user_id'] = sc['filter_id']
        return sc

    def _filter(self, sc):
        return {'label': _('Tất cả phụ trách'),
                'options': self._users_options(self.env['res.users'].search([('share', '=', False)], limit=300))}

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

    def _load(self, sc, since):
        env = self.env
        D = super()._load(sc, since)
        orders = env['sale.order'].sudo().search(self._so_domain(sc) + [('date_order', '>=', since)])
        D['orders'] = [{
            'id': o.id, 'date': dt(o.date_order), 'customer': o.partner_id.commercial_partner_id.id,
            'amount': self._money(o.amount_untaxed, o.currency_id, o.date_order.date(), sc),
        } for o in orders]

        enrolls = env['slide.channel.partner'].sudo().search(self._enroll_domain(sc))
        D['enrolls'] = [{
            'id': e.id, 'partner': e.partner_id.id, 'customer': e.partner_id.commercial_partner_id.id,
            'channel': e.channel_id.id, 'create': dt(e.create_date), 'done': dt(e.dmag_completed_date),
            'status': e.member_status, 'completion': e.completion,
        } for e in enrolls]

        Lesson = env['slide.slide.partner'].sudo()
        act = defaultdict(list)
        D['lessons'] = []
        for row in Lesson.search_read(self._lesson_domain(sc) + [('write_date', '>=', since - sc['stall'])],
                                      ['partner_id', 'channel_id', 'write_date']):
            key = (row['partner_id'] and row['partner_id'][0], row['channel_id'] and row['channel_id'][0])
            act[key].append(dt(row['write_date']))
            D['lessons'].append({'key': key, 'date': dt(row['write_date'])})
        for dates in act.values():
            dates.sort()
        D['act'] = act
        D['last_act'] = {
            (partner.id, channel.id): dt(last)
            for partner, channel, last in Lesson._read_group(
                self._lesson_domain(sc) + [('partner_id', 'in', list({e['partner'] for e in D['enrolls']}))],
                ['partner_id', 'channel_id'], ['write_date:max'])
        }

        tasks = env['project.task'].sudo().search(self._task_domain(sc) + [
            '|', ('dmag_closed_date', '=', False), ('dmag_closed_date', '>=', since)])
        D['tasks'] = [{
            'id': t.id, 'create': dt(t.create_date), 'deadline': t.date_deadline, 'closed': dt(t.dmag_closed_date),
            'state': t.state, 'project': t.project_id.id,
            'customer': (t.project_id.partner_id or t.partner_id).commercial_partner_id.id,
            'users': t.user_ids.ids,
        } for t in tasks]
        return D

    # ------------------------------------------------------------------ metrics
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

    @staticmethod
    def _overdue_rows(D, t):
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

    def _kpi_sub(self, D, key):
        sc = D['sc']
        start, now = sc['start'], sc['now']
        if key == 'contracts':
            amount = sum(o['amount'] for o in D['orders'] if start <= o['date'] < now)
            return {'money': amount, 'prefix': _('Giá trị ')}
        if key == 'enrollments':
            return _('%s khóa được hoàn thành', self._metric(D, 'course_done', start, now))
        if key == 'lessons_done':
            return _('từ %s học viên tích cực', len({x['key'][0] for x in D['lessons'] if start <= x['date'] < now}))
        if key == 'stalled':
            return _('trên %s học viên đang học', sum(1 for e in D['enrolls'] if e['status'] in ('joined', 'ongoing')))
        if key == 'ontime_rate':
            rows = [x for x in D['tasks'] if x['state'] == DONE and x['closed'] and x['deadline']
                    and start <= x['closed'] < now]
            return _('%s/%s đầu việc có deadline', sum(1 for x in rows if x['closed'] <= x['deadline']), len(rows))
        if key == 'overdue':
            rows = self._overdue_rows(D, now)
            worst = max((max(1, (now - x['deadline']).days) for x in rows), default=0)
            return _('trễ nhất %s ngày', worst) if rows else _('Không có đầu việc trễ')
        return ''

    # ------------------------------------------------------------------ journey
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
        for project, count in self.env['project.milestone'].sudo()._read_group(
                [('project_id', 'in', projects.ids), ('is_reached', '=', False), ('deadline', '<', sc['today'])],
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
            ('signed', _('Ký hợp đồng'), lambda c: True),
            ('kickoff', _('Kick-off dự án'), lambda c: bool(projects.get(c))),
            ('learning', _('Bắt đầu học'), lambda c: any(
                e['completion'] > 0 or e['status'] in ('ongoing', 'completed') for e in enrolls[c])),
            ('learned', _('Học xong chương trình'), lambda c: bool(enrolls[c]) and all(
                e['status'] == 'completed' for e in enrolls[c])),
            ('deploying', _('Đang triển khai'), lambda c: any(p['counts']['done'] for p in projects.get(c, []))),
            ('accepted', _('Nghiệm thu'), lambda c: bool(projects.get(c)) and all(
                p['project'].last_update_status == 'done'
                or (p['counts']['total'] and p['counts']['closed'] >= p['counts']['total'])
                for p in projects[c])),
        ]
        out = []
        for key, label, test in stages:
            ids = [c for c in cohort if test(c)]
            out.append({'key': key, 'label': label, 'count': len(ids),
                        'drill': {'type': 'ids', 'model': 'res.partner', 'ids': ids,
                                  'title': _('Khách hàng · %s', label)}})
        speed = self._speed(D, cohort, projects)
        tiles = [
            {'label': _('Ký → Kick-off'), 'value': speed['kickoff_days'], 'unit': 'days',
             'hint': _('Trung bình số ngày từ ngày ký hợp đồng tới ngày bắt đầu dự án')},
            {'label': _('Ký → Bài học đầu tiên'), 'value': speed['first_lesson_days'], 'unit': 'days',
             'hint': _('Trung bình số ngày từ ngày ký tới khi học viên hoàn thành bài học đầu tiên')},
            {'label': _('Chờ kick-off > 7 ngày'), 'value': len(speed['waiting']), 'unit': 'count',
             'alert': bool(speed['waiting']),
             'drill': {'type': 'ids', 'model': 'res.partner', 'ids': speed['waiting'],
                       'title': _('Khách đã ký, chờ kick-off > 7 ngày')}},
        ]
        return {'type': 'funnel', 'id': 'journey', 'title': _('Hành trình khách hàng'),
                'subtitle': _('Khách ký hợp đồng SPDV trong kỳ đã đi tới đâu'), 'stages': out, 'tiles': tiles,
                'empty': _('Chưa có hợp đồng SPDV nào trong kỳ. Thử mở rộng khoảng thời gian.') if not cohort else ''}

    def _speed(self, D, cohort, projects):
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
        first_lesson = {}
        for partner, first in self.env['slide.slide.partner'].sudo()._read_group(
                self._lesson_domain(sc) + [('partner_id.commercial_partner_id', 'in', list(cohort))],
                ['partner_id'], ['write_date:min']):
            c = partner.commercial_partner_id.id
            first_lesson[c] = min(first_lesson.get(c, first), first)
        learn = [max(0, (first_lesson[c] - signed[c]).days) for c in signed if c in first_lesson]
        return {
            'kickoff_days': round(sum(kickoff) / len(kickoff), 1) if kickoff else None,
            'first_lesson_days': round(sum(learn) / len(learn), 1) if learn else None,
            'waiting': [c for c in cohort if not projects.get(c) and sc['now'] - signed[c] > timedelta(days=7)],
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
        rows = []
        for partner in self.env['res.partner'].sudo().browse(list(active)):
            c = partner.id
            r = by_c[c]
            enr = r['enrolls']
            n_stalled = sum(1 for e in enr if e['id'] in stalled)
            plist = projects.get(c, [])
            total = sum(p['counts']['total'] for p in plist)
            closed = sum(p['counts']['closed'] for p in plist)
            late_ms = sum(p['late_milestones'] for p in plist)
            worst_status = max((p['project'].last_update_status for p in plist),
                               key=lambda s: PROJECT_STATUS.get(s, ('', 0))[1], default=False)
            last = max(r['closed_dates']
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
                'id': c, 'name': partner.display_name, 'salesperson': partner.user_id.name or '',
                'score': score, 'level': 'good' if score >= 75 else 'watch' if score >= 50 else 'risk',
                'learners': len({e['partner'] for e in enr}),
                'learn_pct': round(sum(e['completion'] for e in enr) / len(enr)) if enr else None,
                'stalled': n_stalled, 'tasks_total': total, 'tasks_closed': closed,
                'deploy_pct': round(100.0 * closed / total) if total else None,
                'overdue': len(r['overdue']), 'max_late': max_late, 'late_milestones': late_ms,
                'idle_days': (now - last).days if last else None, 'reasons': reasons,
            })
        rows.sort(key=lambda x: (x['score'], -x['overdue'], -x['stalled'], x['name']))
        return rows

    # ------------------------------------------------------------------ panels
    def _rows(self, D):
        sc = D['sc']
        customers = self._customer_rows(D)
        D['customers'] = customers
        summary = {'good': 0, 'watch': 0, 'risk': 0}
        for c in customers:
            summary[c['level']] += 1
        hot = {
            'type': 'table', 'id': 'hotspots', 'title': _('Điểm nóng khách hàng'),
            'subtitle': _('Khách đang học hoặc triển khai, xếp theo điểm sức khỏe thấp nhất · bấm để xem hồ sơ 360°'),
            'summary': [{'tone': 'bad', 'icon': 'fa-exclamation-triangle', 'text': _('%s Nguy cơ', summary['risk'])},
                        {'tone': 'warn', 'icon': 'fa-exclamation-circle', 'text': _('%s Cần chú ý', summary['watch'])},
                        {'tone': 'good', 'icon': 'fa-check-circle', 'text': _('%s Tốt', summary['good'])}],
            'columns': [col('name', _('Khách hàng')), col('score', _('Sức khỏe'), 'score', 'center'),
                        col('learn', _('Tiến độ học'), 'meter'), col('deploy', _('Tiến độ triển khai'), 'meter'),
                        col('overdue', _('Quá hạn'), 'num'), col('stalled', _('Ngưng học'), 'num'),
                        col('idle', _('Hoạt động cuối'), 'ago'), col('reasons', _('Vì sao'), 'chips')],
            'rows': [{
                'id': c['id'], 'open': {'type': 'detail', 'kind': 'customer', 'id': c['id']},
                'cells': {
                    'name': {'v': c['name'], 'sub': _('KD: %s', c['salesperson']) if c['salesperson'] else ''},
                    'score': {'v': c['score'], 'level': c['level']},
                    'learn': {'v': c['learn_pct'], 'sub': _('%s học viên', c['learners']) if c['learners']
                              else _('Chưa ghi danh')},
                    'deploy': {'v': c['deploy_pct'], 'variant': 'cyan',
                               'sub': _('%s/%s đầu việc', c['tasks_closed'], c['tasks_total'])
                               if c['tasks_total'] else _('Chưa kick-off')},
                    'overdue': {'v': c['overdue'] or None, 'tone': 'bad' if c['overdue'] else None},
                    'stalled': {'v': c['stalled'] or None, 'tone': 'bad' if c['stalled'] else None},
                    'idle': {'v': c['idle_days']},
                    'reasons': {'v': c['reasons'], 'empty': _('Ổn định')},
                }} for c in customers[:60]],
            'limit': 8, 'empty': _('Chưa có khách hàng nào đang học hoặc triển khai.'),
        }
        return [
            {'widths': [5, 7], 'panels': [self._funnel(D), self._trend(D, [
                (_('Học'), ['lessons_done', 'enrollments', 'course_done', 'stalled']),
                (_('Triển khai'), ['tasks_done', 'tasks_created', 'overdue'])])]},
            {'widths': [12], 'panels': [hot]},
            {'widths': [6, 6], 'panels': [self._courses_panel(D), self._team_panel(D)]},
        ]

    def _courses_panel(self, D):
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
            n_stalled = sum(1 for e in enr if e['id'] in stalled)
            completed = sum(1 for e in enr if e['status'] == 'completed')
            new = sum(1 for e in enr if start <= e['create'] < now)
            rows.append({
                'id': channel.id, 'open': {'type': 'detail', 'kind': 'course', 'id': channel.id},
                '_sort': (-n_stalled, channel.name),
                'cells': {
                    'name': {'v': channel.name, 'sub': _('%s ghi danh · %s hoàn thành', len(enr), completed)
                             + (_(' · +%s mới', new) if new else '')},
                    'active': sum(1 for e in enr if e['status'] in ('joined', 'ongoing')),
                    'avg': {'v': round(sum(e['completion'] for e in enr) / len(enr)) if enr else 0},
                    'stalled': {'v': n_stalled or None, 'tone': 'bad' if n_stalled else None},
                    'lessons': lessons[channel.id],
                }})
        rows.sort(key=lambda r: r.pop('_sort'))
        return {'type': 'table', 'id': 'courses', 'title': _('Khóa học & chương trình'),
                'subtitle': _('Bấm khóa học để xem từng học viên'),
                'columns': [col('name', _('Khóa học')), col('active', _('Đang học'), 'num'),
                            col('avg', _('Hoàn thành TB'), 'meter'), col('stalled', _('Ngưng'), 'num'),
                            col('lessons', _('Bài xong kỳ này'), 'num')],
                'rows': rows, 'empty': _('Chưa có học viên ghi danh.')}

    def _team_panel(self, D):
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
        names = {u.id: u.name for u in self.env['res.users'].sudo().browse([u for u in stats if u])}
        rows = []
        for uid, s in stats.items():
            if not (s['open'] or s['done']):
                continue
            rate = round(100.0 * s['ontime'] / s['with_deadline']) if s['with_deadline'] else None
            rows.append({
                'id': uid, 'open': {'type': 'user', 'id': uid}, '_sort': (-s['overdue'], -s['open']),
                'cells': {
                    'name': {'v': names.get(uid) or _('Chưa giao việc'), 'avatar': True,
                             'tone': None if uid else 'bad'},
                    'open': s['open'], 'overdue': {'v': s['overdue'] or None, 'tone': 'bad' if s['overdue'] else None},
                    'done': s['done'],
                    'rate': {'v': rate, 'tone': None if rate is None else 'good' if rate >= 85
                             else 'warn' if rate >= 65 else 'bad'},
                }})
        rows.sort(key=lambda r: r.pop('_sort'))
        return {'type': 'table', 'id': 'team', 'title': _('Đội triển khai'),
                'subtitle': _('Tải việc & kỷ luật deadline · bấm để mở danh sách việc'),
                'columns': [col('name', _('Phụ trách')), col('open', _('Đang mở'), 'num'),
                            col('overdue', _('Quá hạn'), 'num'), col('done', _('Xong kỳ này'), 'num'),
                            col('rate', _('Đúng hạn'), 'pct')],
                'rows': rows, 'empty': _('Chưa có đầu việc triển khai.')}

    def _custom_signals(self, D):
        out = []
        risk = [c for c in D.get('customers') or self._customer_rows(D) if c['level'] == 'risk']
        if risk:
            out.append({'severity': 'critical', 'title': _('Khách hàng nguy cơ'),
                        'message': _('%s khách hàng đang ở mức Nguy cơ — thấp nhất: %s (%s điểm).',
                                     len(risk), risk[0]['name'], risk[0]['score']),
                        'drill': {'type': 'detail', 'kind': 'customer', 'id': risk[0]['id']}})
        unassigned = [x['id'] for x in D['tasks'] if not x['users'] and x['state'] not in CLOSED_STATES]
        if unassigned:
            out.append({'severity': 'warning', 'title': _('Đầu việc chưa giao'),
                        'message': _('%s đầu việc triển khai đang mở chưa có người phụ trách.', len(unassigned)),
                        'drill': {'type': 'ids', 'model': 'project.task', 'ids': unassigned,
                                  'title': _('Đầu việc chưa giao')}})
        return out

    def _extra_glossary(self, D):
        return [
            {'key': 'health', 'label': _('Điểm sức khỏe'),
             'hint': _('100 điểm, trừ: 6/đầu việc quá hạn (tối đa 36), 8/học viên ngưng học (tối đa 32), '
                       '10/mốc nghiệm thu trễ, 10–20 theo trạng thái dự án báo cáo, 15 nếu không hoạt động quá '
                       '%s ngày. ≥75 Tốt · 50–74 Cần chú ý · <50 Nguy cơ.', D['sc']['idle'].days)},
            {'key': 'stall', 'label': _('Ngưỡng ngưng học'),
             'hint': _('Hiện tại: %s ngày (đổi trong Cấu hình).', D['sc']['stall'].days)},
        ]

    # ------------------------------------------------------------------ summary for the executive board
    def _summary(self, D):
        rows = D.get('customers') or self._customer_rows(D)
        return [self._kpi(D, 'contracts'), self._kpi(D, 'stalled'), self._kpi(D, 'overdue')], {
            'label': _('Khách hàng nguy cơ'), 'value': sum(1 for c in rows if c['level'] == 'risk'),
            'unit': 'count'}

    # ------------------------------------------------------------------ drill-down
    def _drill(self, spec, sc):
        kind = spec.get('type')
        start, now = fields.Datetime.to_string(sc['start']), fields.Datetime.to_string(sc['now'])
        task_views = [(self.env.ref('project.view_task_tree2').id, 'list'), (False, 'kanban'), (False, 'form')]
        by_project = {'group_by': ['project_id']}
        by_course = {'group_by': ['channel_id']}
        if kind == 'kpi':
            key = spec['key']
            name = self._dmag_metrics()[key]['label']
            if key == 'contracts':
                return self._list_action('sale.order', self._so_domain(sc) + [('date_order', '>=', start)], name)
            if key == 'enrollments':
                return self._list_action('slide.channel.partner', self._enroll_domain(sc) + [
                    ('create_date', '>=', start)], name, by_course)
            if key == 'course_done':
                return self._list_action('slide.channel.partner', self._enroll_domain(sc) + [
                    ('dmag_completed_date', '>=', start)], name, by_course)
            if key == 'lessons_done':
                return self._list_action('slide.channel.partner', self._enroll_domain(sc) + [
                    ('member_status', 'in', ('ongoing', 'completed')), ('write_date', '>=', start)],
                    _('Học viên có tiến độ trong kỳ'), by_course)
            if key == 'stalled':
                D = self._load(sc, sc['start'])
                return self._list_action('slide.channel.partner', [('id', 'in', self._stalled_ids(D, sc['now']))],
                                         name, by_course)
            if key in ('tasks_done', 'ontime_rate'):
                domain = self._task_domain(sc) + [('state', '=', DONE), ('dmag_closed_date', '>=', start)]
                if key == 'ontime_rate':
                    domain.append(('date_deadline', '!=', False))
                    name = _('Đầu việc hoàn thành có deadline')
                return self._list_action('project.task', domain, name, by_project, task_views)
            if key == 'tasks_created':
                return self._list_action('project.task', self._task_domain(sc) + [('create_date', '>=', start)],
                                         name, by_project, task_views)
            if key == 'overdue':
                return self._list_action('project.task', self._task_domain(sc) + [
                    ('state', 'not in', list(CLOSED_STATES)), ('date_deadline', '<', now)], name, by_project,
                    task_views)
        if kind == 'course':
            return self._list_action('slide.channel.partner', self._enroll_domain(sc) + [
                ('channel_id', '=', int(spec['id']))], spec.get('title') or '')
        if kind == 'user':
            uid = int(spec['id'])
            domain = self._task_domain(dict(sc, user_id=0)) + [('state', 'not in', list(CLOSED_STATES))]
            domain.append(('user_ids', 'in', [uid]) if uid else ('user_ids', '=', False))
            return self._list_action('project.task', domain, _('Đầu việc đang mở'), by_project, task_views)
        if kind == 'project':
            return self._list_action('project.task', [('project_id', '=', int(spec['id']))],
                                     spec.get('title') or '', {'search_default_open_tasks': 1}, task_views)
        return None

    # ------------------------------------------------------------------ 360° drawers
    def _learner_item(self, e, D, stalled, name, link=None):
        now = D['sc']['now']
        last = D['last_act'].get((e['partner'], e['channel']))
        idle = (now - (last or e['create'])).days
        return {
            'title': name, 'badge': {'tone': 'bad', 'text': _('Ngưng %s ngày', idle)} if e['id'] in stalled else None,
            'link': link, 'sub': f"{ENROLL_STATUS.get(e['status'], '')} · {_('hoạt động')} {ago(idle)}",
            'meter': {'pct': e['completion']},
            'drill': {'type': 'record', 'model': 'slide.channel.partner', 'id': e['id']},
            '_sort': (e['id'] not in stalled, e['status'] == 'completed', e['completion']),
        }

    def _detail(self, kind, res_id, sc):
        D = self._load(sc, sc['prev_start'])
        stalled = set(self._stalled_ids(D, sc['now']))
        partners = self.env['res.partner'].sudo()
        if kind == 'course':
            channel = self.env['slide.channel'].sudo().browse(res_id)
            items = []
            for e in D['enrolls']:
                if e['channel'] == channel.id:
                    p = partners.browse(e['partner'])
                    items.append(self._learner_item(e, D, stalled, p.name, link={
                        'text': p.commercial_partner_id.name,
                        'drill': {'type': 'detail', 'kind': 'customer', 'id': p.commercial_partner_id.id}}))
            items.sort(key=lambda i: i.pop('_sort'))
            return {
                'eyebrow': _('Khóa học'), 'title': channel.name,
                'subtitle': _('%s bài học', channel.total_slides) + (
                    _(' · Phụ trách: %s', channel.user_id.name) if channel.user_id else ''),
                'actions': [{'label': _('Danh sách học viên trong Odoo'), 'icon': 'fa-list',
                             'drill': {'type': 'course', 'id': channel.id, 'title': channel.name}}],
                'sections': [{'title': _('Học viên'), 'items': items}],
            }

        partner = partners.browse(res_id).commercial_partner_id
        now = sc['now']
        rows = self._customer_rows(D, only={partner.id})
        health = rows[0] if rows else None
        channels = {c.id: c.name for c in self.env['slide.channel'].sudo().browse(
            list({e['channel'] for e in D['enrolls'] if e['customer'] == partner.id}))}
        learners = []
        for e in D['enrolls']:
            if e['customer'] == partner.id:
                item = self._learner_item(e, D, stalled, partners.browse(e['partner']).name)
                item['sub'] = f"{channels.get(e['channel'], '')} · {item['sub']}"
                learners.append(item)
        learners.sort(key=lambda i: i.pop('_sort'))

        projects = []
        plist = self._projects_by_customer(D, {partner.id}).get(partner.id, [])
        for p in plist:
            proj = p['project']
            nxt = proj.milestone_ids.filtered(lambda m: not m.is_reached and m.deadline).sorted('deadline')[:1]
            status = PROJECT_STATUS.get(proj.last_update_status, ('', 0, None))
            sub = (_('PM: %s · ', proj.user_id.name) if proj.user_id else '') + \
                _('%s/%s đầu việc', p['counts']['closed'], p['counts']['total'])
            if nxt:
                sub += _(' · Mốc tới: %s (%s)', nxt.name, nxt.deadline.strftime('%d/%m'))
            if p['late_milestones']:
                sub += _(' · %s mốc trễ', p['late_milestones'])
            total = p['counts']['total']
            projects.append({
                'title': proj.name, 'badge': {'tone': status[2], 'text': status[0]} if status[0] else None,
                'sub': sub, 'meter': {'pct': round(100.0 * p['counts']['closed'] / total) if total else 0,
                                      'variant': 'cyan'},
                'drill': {'type': 'project', 'id': proj.id, 'title': proj.name}})

        tasks = self.env['project.task'].sudo().search(
            self._task_domain(dict(sc, user_id=0)) + [
                ('project_id', 'in', [p['project'].id for p in plist]),
                ('state', 'not in', list(CLOSED_STATES))], order='date_deadline asc', limit=20)
        task_items = []
        for t in tasks:
            late = max(1, (now - t.date_deadline).days) if t.date_deadline and t.date_deadline < now else 0
            task_items.append({
                'title': t.name,
                'sub': ' · '.join(filter(None, [t.project_id.name, t.stage_id.name, ', '.join(t.user_ids.mapped('name'))])),
                'value': {'text': _('Trễ %s ngày', late), 'tone': 'bad'} if late else {
                    'text': t.date_deadline.strftime('%d/%m') if t.date_deadline else _('Không deadline')},
                'drill': {'type': 'record', 'model': 'project.task', 'id': t.id}})
        orders = self.env['sale.order'].sudo().search(
            self._so_domain(dict(sc, user_id=0)) + [('partner_id', 'child_of', partner.id)],
            order='date_order desc', limit=10)
        order_items = [{'title': o.name, 'sub': o.date_order.strftime('%d/%m/%Y'),
                        'value': {'v': o.amount_untaxed, 'unit': 'money'},
                        'drill': {'type': 'record', 'model': 'sale.order', 'id': o.id}} for o in orders]
        contact = ' · '.join(filter(None, [_('Kinh doanh: %s', partner.user_id.name) if partner.user_id else '',
                                           partner.phone or partner.mobile, partner.email]))
        return {
            'eyebrow': _('Hồ sơ khách hàng 360°'), 'title': partner.display_name, 'subtitle': contact,
            'actions': [{'label': _('Mở hồ sơ trong Odoo'), 'icon': 'fa-external-link',
                         'drill': {'type': 'record', 'model': 'res.partner', 'id': partner.id}}],
            'gauge': {'score': health['score'], 'level': health['level']} if health else None,
            'stats': [
                {'label': _('Hoàn thành học'), 'value': health['learn_pct'], 'unit': 'percent'},
                {'label': _('Tiến độ triển khai'), 'value': health['deploy_pct'], 'unit': 'percent'},
                {'label': _('Việc quá hạn'), 'value': health['overdue'], 'unit': 'count',
                 'tone': 'bad' if health['overdue'] else None},
                {'label': _('Ngưng học'), 'value': health['stalled'], 'unit': 'count',
                 'tone': 'bad' if health['stalled'] else None},
            ] if health else [],
            'chips': health['reasons'] if health else [],
            'empty': '' if health else _('Khách hàng hiện không có hoạt động học/triển khai đang mở.'),
            'sections': [
                {'title': _('Học viên'), 'items': learners, 'empty': _('Chưa có học viên ghi danh.')},
                {'title': _('Dự án triển khai'), 'items': projects, 'empty': _('Chưa kick-off dự án.')},
                {'title': _('Đầu việc đang mở'), 'items': task_items, 'empty': _('Không có đầu việc mở.')},
                {'title': _('Hợp đồng SPDV'), 'items': order_items, 'empty': _('Chưa có hợp đồng.')},
            ],
        }
