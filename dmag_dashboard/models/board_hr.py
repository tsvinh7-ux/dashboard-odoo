from collections import defaultdict
from datetime import timedelta

from odoo import api, fields, models, _

from .board_base import col, dt, to_dt


class DmagBoardHr(models.AbstractModel):
    _name = 'dmag.board.hr'
    _inherit = 'dmag.board'
    _description = 'DMAG Board · Nhân sự'

    _dmag_key = 'hr'
    _dmag_title = 'Nhân sự'
    _dmag_icon = 'fa-users'
    _dmag_sequence = 60
    _dmag_group = 'dmag_dashboard.group_dmag_role_hr'
    _dmag_requires = ('hr.employee',)
    _dmag_action = 'dmag_dashboard.action_dmag_board_hr'

    def _has(self, model):
        return model in self.env

    @api.model
    def _dmag_metrics(self):
        res = {
            'headcount': {'label': 'Nhân sự hiện tại', 'tag': 'Đội ngũ', 'accent': 'navy', 'kind': 'state',
                          'unit': 'count', 'good': None, 'hint': 'Số nhân viên đang làm việc (tại cuối kỳ).'},
            'new_hires': {'label': 'Nhân sự mới', 'tag': 'Đội ngũ', 'unit': 'count', 'good': None,
                          'hint': 'Nhân viên bắt đầu làm việc trong kỳ (ngày hợp đồng đầu tiên).'},
            'departures': {'label': 'Nghỉ việc', 'tag': 'Giữ chân', 'unit': 'count', 'good': 'down',
                           'hint': 'Nhân viên nghỉ việc trong kỳ (ngày nghỉ việc hoặc ngày lưu trữ hồ sơ).'},
            'turnover_rate': {'label': 'Tỷ lệ nghỉ việc', 'tag': 'Giữ chân', 'kind': 'ratio', 'unit': 'percent',
                              'good': 'down', 'hint': 'Số nghỉ việc trong kỳ / nhân sự bình quân đầu & cuối kỳ.'},
        }
        if self._has('hr.applicant'):
            res.update({
                'applicants_new': {'label': 'Ứng viên mới', 'tag': 'Tuyển dụng', 'accent': 'cyan', 'unit': 'count',
                                   'good': 'up', 'hint': 'Hồ sơ ứng tuyển nhận được trong kỳ.'},
                'hired': {'label': 'Tuyển thành công', 'tag': 'Tuyển dụng', 'accent': 'cyan', 'unit': 'count',
                          'good': 'up', 'hint': 'Ứng viên chuyển sang giai đoạn đã tuyển trong kỳ.'},
                'time_to_hire': {'label': 'Thời gian tuyển TB', 'tag': 'Tuyển dụng', 'accent': 'cyan',
                                 'kind': 'ratio', 'unit': 'days', 'good': 'down',
                                 'hint': 'Số ngày trung bình từ lúc nhận hồ sơ tới lúc tuyển, với ứng viên tuyển trong kỳ.'},
            })
        return res

    @api.model
    def _kpi_order(self):
        keys = ['headcount', 'new_hires', 'departures', 'turnover_rate']
        if self._has('hr.applicant'):
            keys += ['applicants_new', 'time_to_hire']
        return keys

    def _filter(self, sc):
        deps = self.env['hr.department'].search([('company_id', 'in', sc['company_ids'] + [False])])
        return {'label': _('Mọi phòng ban'), 'options': [{'id': d.id, 'name': d.complete_name} for d in deps]}

    def _emp_domain(self, sc):
        dom = [('company_id', 'in', sc['company_ids'])]
        if sc['filter_id']:
            dom.append(('department_id', 'child_of', sc['filter_id']))
        return dom

    def _load(self, sc, since):
        D = super()._load(sc, since)
        env = self.env
        Emp = env['hr.employee'].sudo().with_context(active_test=False)
        emps = Emp.search(self._emp_domain(sc))
        has_first = 'first_contract_date' in Emp._fields and Emp._fields['first_contract_date'].store
        D['emps'] = []
        for e in emps:
            start = to_dt(e.first_contract_date) if has_first and e.first_contract_date else dt(e.create_date)
            leave = to_dt(e.departure_date) if e.departure_date else (None if e.active else dt(e.write_date))
            D['emps'].append({'id': e.id, 'name': e.name, 'dept': e.department_id.id, 'job': e.job_id.id,
                              'start': start, 'leave': leave, 'active': e.active,
                              'calendar_hours': (e.resource_calendar_id.hours_per_day or 8.0) * 5})
        D['applicants'] = []
        if self._has('hr.applicant'):
            App = env['hr.applicant'].sudo().with_context(active_test=False)
            dom = [('company_id', 'in', sc['company_ids'] + [False]),
                   '|', '|', ('create_date', '>=', since), ('date_closed', '>=', since), ('active', '=', True)]
            if sc['filter_id']:
                dom.append(('job_id.department_id', 'child_of', sc['filter_id']))
            for a in App.search(dom):
                D['applicants'].append({
                    'id': a.id, 'create': dt(a.create_date), 'hired': dt(a.date_closed), 'active': a.active,
                    'stage': a.stage_id.id, 'job': a.job_id.id, 'last_stage': dt(a.date_last_stage_update),
                    'refused': bool(a.refuse_reason_id) or not a.active,
                })
        D['contracts'] = env['hr.contract']
        if self._has('hr.contract'):
            soon = sc['today'] + timedelta(days=45)
            D['contracts'] = env['hr.contract'].sudo().search([
                ('state', '=', 'open'), ('employee_id', 'in', [e['id'] for e in D['emps'] if e['active']]),
                '|', '&', ('date_end', '!=', False), ('date_end', '<=', soon),
                '&', ('trial_date_end', '!=', False), '&', ('trial_date_end', '<=', soon),
                ('trial_date_end', '>=', sc['today'])], order='date_end')
        D['hours'] = {}
        Line = env['account.analytic.line']
        if 'employee_id' in Line._fields and 'project_id' in Line._fields:
            week = sc['today'] - timedelta(days=7)
            for emp, hours in Line.sudo()._read_group(
                    [('project_id', '!=', False), ('date', '>', week), ('date', '<=', sc['today']),
                     ('employee_id', 'in', [e['id'] for e in D['emps'] if e['active']])],
                    ['employee_id'], ['unit_amount:sum']):
                D['hours'][emp.id] = hours
            # only people who normally log time (any timesheet in the last 60 days) are expected to
            D['timesheet_emps'] = {emp.id for [emp] in Line.sudo()._read_group(
                [('project_id', '!=', False), ('date', '>', sc['today'] - timedelta(days=60))], ['employee_id'])}
            D['timesheets'] = bool(D['timesheet_emps'])
        D['on_leave'] = []
        if self._has('hr.leave'):
            D['on_leave'] = env['hr.leave'].sudo().search([
                ('state', '=', 'validate'), ('date_from', '<=', sc['now']), ('date_to', '>=', sc['now']),
                ('employee_id', 'in', [e['id'] for e in D['emps'] if e['active']])]).ids
        return D

    # ------------------------------------------------------------------ metrics
    @staticmethod
    def _employed_at(e, t):
        return e['start'] and e['start'] <= t and (not e['leave'] or e['leave'] > t)

    def _metric(self, D, key, start, end):
        if key == 'headcount':
            return sum(1 for e in D['emps'] if self._employed_at(e, end))
        if key == 'new_hires':
            return sum(1 for e in D['emps'] if e['start'] and start <= e['start'] < end)
        if key == 'departures':
            return sum(1 for e in D['emps'] if e['leave'] and start <= e['leave'] < end)
        if key == 'turnover_rate':
            avg = (self._metric(D, 'headcount', start, start) + self._metric(D, 'headcount', start, end)) / 2.0
            return round(100.0 * self._metric(D, 'departures', start, end) / avg, 1) if avg else None
        if key == 'applicants_new':
            return sum(1 for a in D['applicants'] if start <= a['create'] < end)
        if key == 'hired':
            return sum(1 for a in D['applicants'] if a['hired'] and start <= a['hired'] < end)
        if key == 'time_to_hire':
            rows = [(a['hired'] - a['create']).days for a in D['applicants'] if a['hired'] and start <= a['hired'] < end]
            return round(sum(rows) / len(rows), 1) if rows else None
        raise ValueError(key)

    def _kpi_sub(self, D, key):
        sc = D['sc']
        if key == 'headcount':
            if not self._has('hr.leave'):
                return ''
            return _('%s người đang nghỉ phép hôm nay', len(D['on_leave'])) if D['on_leave'] \
                else _('Không ai nghỉ phép hôm nay')
        if key == 'applicants_new':
            return _('%s đã tuyển trong kỳ', self._metric(D, 'hired', sc['start'], sc['now']))
        if key == 'turnover_rate':
            return _('Quy năm ≈ %s%%', round((self._metric(D, 'turnover_rate', sc['start'], sc['now']) or 0)
                                            * 365 / sc['period'], 1))
        return ''

    # ------------------------------------------------------------------ panels
    def _rows(self, D):
        sc = D['sc']
        now = sc['now']
        current = [e for e in D['emps'] if self._employed_at(e, now)]
        by_dept = defaultdict(list)
        for e in current:
            by_dept[e['dept']].append(e['id'])
        depts = {d.id: d.name for d in self.env['hr.department'].sudo().browse([d for d in by_dept if d])}
        headcount = {'type': 'bars', 'id': 'depts', 'title': _('Nhân sự theo phòng ban'), 'unit': 'count',
                     'subtitle': _('Biên chế hiện tại · bấm để xem danh sách'),
                     'items': [{'label': depts.get(d) or _('Chưa gán phòng ban'), 'value': len(ids),
                                'drill': {'type': 'ids', 'model': 'hr.employee', 'ids': ids,
                                          'title': depts.get(d) or _('Chưa gán phòng ban')}}
                               for d, ids in sorted(by_dept.items(), key=lambda kv: -len(kv[1]))],
                     'empty': _('Chưa có hồ sơ nhân viên.')}
        rows = []
        if self._has('hr.applicant'):
            rows.append({'widths': [5, 7], 'panels': [headcount, self._recruit_funnel(D)]})
            rows.append({'widths': [7, 5], 'panels': [self._jobs(D), self._attention(D)]})
        else:
            rows.append({'widths': [5, 7], 'panels': [headcount, self._attention(D)]})
        groups = [(_('Đội ngũ'), ['headcount', 'new_hires', 'departures'])]
        if self._has('hr.applicant'):
            groups.append((_('Tuyển dụng'), ['applicants_new', 'hired']))
        trend = self._trend(D, groups)
        if D.get('timesheets'):
            loggers = [e for e in current if e['id'] in D['timesheet_emps']]
            rows.append({'widths': [7, 5], 'panels': [trend, self._timesheets(D, loggers)]})
        else:
            rows.append({'widths': [12], 'panels': [trend]})
        return rows

    def _recruit_funnel(self, D):
        sc = D['sc']
        ongoing = [a for a in D['applicants'] if a['active'] and not a['hired'] and not a['refused']]
        stages = self.env['hr.recruitment.stage'].sudo().search([], order='sequence, id')
        by = defaultdict(list)
        for a in ongoing:
            by[a['stage']].append(a['id'])
        waiting = [a['id'] for a in ongoing if a['last_stage'] and sc['now'] - a['last_stage'] > timedelta(days=7)]
        jobs = {a['job'] for a in ongoing if a['job']}
        hired = [a['id'] for a in D['applicants'] if a['hired'] and sc['start'] <= a['hired'] < sc['now']]
        return {
            'type': 'funnel', 'id': 'recruit', 'title': _('Phễu tuyển dụng'), 'distribution': True,
            'subtitle': _('Ứng viên đang xử lý ở từng vòng'),
            'stages': [{'key': s.id, 'label': s.name, 'count': len(by[s.id]),
                        'drill': {'type': 'ids', 'model': 'hr.applicant', 'ids': by[s.id], 'title': s.name}}
                       for s in stages if not s.hired_stage],
            'tiles': [
                {'label': _('Vị trí đang tuyển'), 'value': len(jobs), 'unit': 'count'},
                {'label': _('Ứng viên chờ > 7 ngày'), 'value': len(waiting), 'unit': 'count', 'alert': bool(waiting),
                 'drill': {'type': 'ids', 'model': 'hr.applicant', 'ids': waiting,
                           'title': _('Ứng viên đứng một vòng quá 7 ngày')}},
                {'label': _('Tuyển thành công kỳ này'), 'value': len(hired), 'unit': 'count',
                 'drill': {'type': 'ids', 'model': 'hr.applicant', 'ids': hired, 'title': _('Đã tuyển trong kỳ'),
                           'context': {'active_test': False}}},
            ],
            'empty': '' if ongoing else _('Không có ứng viên đang xử lý.'),
        }

    def _jobs(self, D):
        sc = D['sc']
        stats = defaultdict(lambda: {'ongoing': 0, 'new': 0, 'hired': 0, 'waiting': 0})
        for a in D['applicants']:
            if not a['job']:
                continue
            s = stats[a['job']]
            if a['active'] and not a['hired'] and not a['refused']:
                s['ongoing'] += 1
                s['waiting'] += bool(a['last_stage'] and sc['now'] - a['last_stage'] > timedelta(days=7))
            if sc['start'] <= a['create'] < sc['now']:
                s['new'] += 1
            if a['hired'] and sc['start'] <= a['hired'] < sc['now']:
                s['hired'] += 1
        jobs = self.env['hr.job'].sudo().browse(list(stats))
        rows = []
        for job in jobs:
            s = stats[job.id]
            if not (s['ongoing'] or s['new'] or s['hired']):
                continue
            rows.append({'id': job.id, 'open': {'type': 'job', 'id': job.id, 'title': job.name},
                         'cells': {'name': {'v': job.name, 'sub': job.department_id.name or ''},
                                   'target': job.no_of_recruitment or None, 'new': s['new'],
                                   'ongoing': s['ongoing'], 'hired': s['hired'],
                                   'waiting': {'v': s['waiting'] or None, 'tone': 'warn' if s['waiting'] else None}}})
        rows.sort(key=lambda r: -r['cells']['ongoing'])
        return {'type': 'table', 'id': 'jobs', 'title': _('Vị trí đang tuyển'),
                'subtitle': _('Chỉ tiêu, hồ sơ mới, đang xử lý và đã tuyển trong kỳ · bấm để mở ứng viên'),
                'columns': [col('name', _('Vị trí')), col('target', _('Chỉ tiêu'), 'num'),
                            col('new', _('Hồ sơ mới'), 'num'), col('ongoing', _('Đang xử lý'), 'num'),
                            col('hired', _('Đã tuyển'), 'num'), col('waiting', _('Chờ lâu'), 'num')],
                'rows': rows, 'empty': _('Không có vị trí đang tuyển.')}

    def _attention(self, D):
        today = D['sc']['today']
        rows = []
        for c in D['contracts']:
            if c.trial_date_end and today <= c.trial_date_end <= today + timedelta(days=45) and \
                    (not c.date_end or c.trial_date_end < c.date_end):
                what, day = _('Hết thử việc'), c.trial_date_end
            else:
                what, day = _('Hết hạn hợp đồng'), c.date_end
            if not day:
                continue
            left = (day - today).days
            rows.append({'id': c.id, 'open': {'type': 'record', 'model': 'hr.contract', 'id': c.id},
                         '_sort': left,
                         'cells': {'name': {'v': c.employee_id.name, 'sub': c.department_id.name or ''},
                                   'what': what,
                                   'left': {'v': _('Quá %s ngày', -left) if left < 0 else _('còn %s ngày', left),
                                            'tone': 'bad' if left < 7 else 'warn' if left < 21 else None}}})
        rows.sort(key=lambda r: r.pop('_sort'))
        return {'type': 'table', 'id': 'attention', 'title': _('Cần xử lý'),
                'subtitle': _('Hợp đồng sắp hết hạn & thử việc sắp kết thúc trong 45 ngày'),
                'columns': [col('name', _('Nhân viên')), col('what', _('Việc')), col('left', _('Hạn'))],
                'rows': rows, 'limit': 8,
                'empty': _('Không có hợp đồng cần xử lý.') if self._has('hr.contract')
                else _('Cài ứng dụng Hợp đồng nhân viên để theo dõi.')}

    def _timesheets(self, D, current):
        rows = []
        for e in current:
            hours = D['hours'].get(e['id'], 0.0)
            pct = round(100.0 * hours / e['calendar_hours']) if e['calendar_hours'] else None
            rows.append({'id': e['id'], 'open': {'type': 'record', 'model': 'hr.employee', 'id': e['id']},
                         'cells': {'name': {'v': e['name'], 'avatar': True},
                                   'hours': {'v': round(hours, 1), 'tone': 'bad' if not hours else None},
                                   'pct': {'v': min(pct, 100) if pct is not None else None,
                                           'variant': 'bad' if pct is not None and pct < 60 else None}}})
        rows.sort(key=lambda r: r['cells']['hours']['v'])
        return {'type': 'table', 'id': 'timesheets', 'title': _('Ghi nhận giờ công 7 ngày'),
                'subtitle': _('Giờ chấm công dự án so với chuẩn tuần — thấp = thiếu dữ liệu hoặc thiếu việc'),
                'columns': [col('name', _('Nhân viên')), col('hours', _('Giờ'), 'hours'),
                            col('pct', _('So với chuẩn'), 'meter')],
                'rows': rows, 'limit': 8, 'empty': _('Chưa có chấm công.')}

    def _custom_signals(self, D):
        out = []
        today = D['sc']['today']
        ending = D['contracts'].filtered(lambda c: c.date_end and c.date_end <= today + timedelta(days=14))
        if ending:
            out.append({'severity': 'warning', 'title': _('Hợp đồng sắp hết hạn'),
                        'message': _('%s hợp đồng lao động hết hạn trong 14 ngày tới.', len(ending)),
                        'drill': {'type': 'ids', 'model': 'hr.contract', 'ids': ending.ids,
                                  'title': _('Hợp đồng sắp hết hạn')}})
        return out

    def _summary(self, D):
        keys = ['headcount', 'departures'] + (['applicants_new'] if self._has('hr.applicant') else ['new_hires'])
        return [self._kpi(D, k) for k in keys], None

    def _drill(self, spec, sc):
        kind = spec.get('type')
        start = sc['start']
        if kind == 'kpi':
            key = spec['key']
            name = self._dmag_metrics()[key]['label']
            if key in ('headcount', 'new_hires', 'departures', 'turnover_rate'):
                D = self._load(sc, sc['start'])
                if key == 'headcount':
                    ids = [e['id'] for e in D['emps'] if self._employed_at(e, sc['now'])]
                elif key == 'new_hires':
                    ids = [e['id'] for e in D['emps'] if e['start'] and e['start'] >= start]
                else:
                    ids = [e['id'] for e in D['emps'] if e['leave'] and e['leave'] >= start]
                return self._list_action('hr.employee', [('id', 'in', ids)], name,
                                         {'active_test': False, 'group_by': ['department_id']})
            if key == 'applicants_new':
                return self._list_action('hr.applicant', [('create_date', '>=', fields.Datetime.to_string(start))],
                                         name, {'active_test': False, 'group_by': ['job_id']})
            if key in ('hired', 'time_to_hire'):
                return self._list_action('hr.applicant', [('date_closed', '>=', fields.Datetime.to_string(start))],
                                         name, {'group_by': ['job_id']})
        if kind == 'job':
            return self._list_action('hr.applicant', [('job_id', '=', int(spec['id']))], spec.get('title') or '',
                                     {'group_by': ['stage_id']})
        return None
