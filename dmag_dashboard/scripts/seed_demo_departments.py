# -*- coding: utf-8 -*-
"""Dữ liệu DEMO cho các bảng phòng ban (Kinh doanh, Marketing, Tài chính, Nhân sự, Mua hàng & Kho).

CHỈ CHẠY TRÊN DATABASE THỬ NGHIỆM. Chạy SAU seed_demo_data.py:

    ./odoo-bin shell -c odoo.conf -d <db_thu_nghiem> < dmag_dashboard/scripts/seed_demo_departments.py

Mỗi phần chỉ chạy khi ứng dụng tương ứng đã được cài (crm, account, hr, hr_recruitment, hr_contract,
hr_timesheet, event, purchase_stock).
"""
import random
import traceback
from datetime import timedelta

from odoo import fields

rng = random.Random(2026)
E = env  # noqa: F821  (provided by odoo shell)
cr = E.cr
now = fields.Datetime.now()
today = fields.Date.today()


def ago(days, hours=0):
    return now - timedelta(days=days, hours=hours)


def backdate(model, ids, values):
    E.flush_all()
    table = E[model]._table
    sets = ', '.join(f'"{k}" = %s' for k in values)
    cr.execute(f'UPDATE "{table}" SET {sets} WHERE id = ANY(%s)', list(values.values()) + [list(ids)])
    E.invalidate_all()


def section(name):
    def deco(fn):
        try:
            fn()
            cr.commit()
            print(f'[ok] {name}')
        except Exception:
            cr.rollback()
            print(f'[FAILED] {name}')
            traceback.print_exc()
        return fn
    return deco


staff = E['res.users'].search([('login', 'in', ['thu.dmag', 'vinh.dmag', 'khoa.dmag', 'ngoc.dmag'])])
sellers = staff[:2] if staff else E.user
first = ['Anh', 'Bình', 'Chi', 'Dũng', 'Giang', 'Hà', 'Hải', 'Hùng', 'Khánh', 'Lan', 'Linh', 'Long', 'Mai', 'Minh',
         'Nam', 'Ngân', 'Nhung', 'Phúc', 'Quân', 'Sơn', 'Thảo', 'Trang', 'Tú', 'Tuấn', 'Vy', 'Yến']
last = ['Nguyễn', 'Trần', 'Lê', 'Phạm', 'Hoàng', 'Vũ', 'Đặng', 'Bùi', 'Đỗ', 'Ngô']
mid = ['Văn', 'Thị', 'Minh', 'Thu', 'Hữu', 'Ngọc', 'Quang', 'Thanh']


def person():
    return f'{rng.choice(last)} {rng.choice(mid)} {rng.choice(first)}'


# ---------------------------------------------------------------------------------------------- CRM
@section('CRM & Marketing')
def seed_crm():
    if 'crm.lead' not in E:
        return
    Utm = {m: E[m] for m in ('utm.source', 'utm.medium', 'utm.campaign')}

    def utm(model, name):
        return Utm[model].search([('name', '=', name)], limit=1) or Utm[model].create({'name': name})

    channels = [('Facebook Ads', 'Mạng xã hội', 3), ('Google Search', 'Tìm kiếm', 3), ('Giới thiệu', 'Giới thiệu', 2),
                ('Hội thảo Định Vị Mới', 'Sự kiện', 2), ('Website', 'Tìm kiếm', 2), ('Zalo OA', 'Mạng xã hội', 1)]
    campaigns = [utm('utm.campaign', n) for n in ('Định Vị Mới Q3', 'Webinar Thương hiệu 2026', 'Tri ân khách cũ')]
    stages = E['crm.stage'].search([], order='sequence')
    open_stages = stages.filtered(lambda s: not s.is_won)
    won_stage = stages.filtered('is_won')[:1]
    reasons = [E['crm.lost.reason'].search([('name', '=', n)], limit=1) or E['crm.lost.reason'].create({'name': n})
               for n in ('Giá cao so với ngân sách', 'Chọn đối thủ', 'Chưa đến thời điểm', 'Không phản hồi')]
    companies = ['Nha khoa', 'Spa', 'Phòng khám', 'Trường mầm non', 'Nội thất', 'Thời trang', 'Logistics', 'Dược',
                 'Mỹ phẩm', 'Bất động sản', 'Nhà hàng', 'Gym']
    leads = []
    for i in range(170):
        src, med, weight = rng.choices(channels, weights=[c[2] for c in channels])[0]
        age = rng.uniform(0, 180)
        is_opp = rng.random() < (0.75 if src in ('Giới thiệu', 'Hội thảo Định Vị Mới') else 0.55)
        user = rng.choice(sellers) if rng.random() > 0.08 else E['res.users']
        vals = {
            'name': f'{rng.choice(["Tư vấn định vị", "Gói Định Vị Mới", "Đào tạo đội ngũ", "Triển khai Odoo"])} – '
                    f'{rng.choice(companies)} {rng.choice(first)}',
            'partner_name': f'Công ty {rng.choice(companies)} {rng.choice(first)} {i}',
            'contact_name': person(), 'type': 'opportunity' if is_opp else 'lead',
            'source_id': utm('utm.source', src).id, 'medium_id': utm('utm.medium', med).id,
            'campaign_id': rng.choice(campaigns).id if rng.random() < 0.45 else False,
            'expected_revenue': rng.choice([90, 150, 180, 220, 280, 350, 420]) * 1_000_000,
            'user_id': user.id,
        }
        lead = E['crm.lead'].create(vals)
        created = ago(age, rng.randint(0, 9))
        leads.append(lead)
        if not is_opp:
            backdate('crm.lead', lead.ids, {'create_date': created, 'date_last_stage_update': created})
            continue
        dur = rng.uniform(7, 70)
        closed = created + timedelta(days=dur)
        outcome = rng.random()
        if closed < now and outcome < 0.38:
            lead.write({'stage_id': won_stage.id})
            backdate('crm.lead', lead.ids, {'create_date': created, 'date_closed': closed,
                                             'date_last_stage_update': closed})
        elif closed < now and outcome < 0.78:
            lead.action_set_lost(lost_reason_id=rng.choice(reasons).id)
            backdate('crm.lead', lead.ids, {'create_date': created, 'date_closed': closed, 'write_date': closed,
                                             'date_last_stage_update': closed})
        else:
            lead.write({'stage_id': rng.choice(open_stages).id,
                        'date_deadline': (created + timedelta(days=dur)).date()})
            last_move = created + timedelta(days=rng.uniform(0, max(1.0, (now - created).days)))
            backdate('crm.lead', lead.ids, {'create_date': created, 'date_last_stage_update': last_move})
            if rng.random() < 0.6:
                lead.activity_schedule('mail.mail_activity_data_call',
                                       date_deadline=today + timedelta(days=rng.randint(-10, 7)),
                                       user_id=(lead.user_id or E.user).id)

    # quotations waiting for an answer
    products = E['product.product'].search([('categ_id.name', '=', 'SPDV DMAG')])
    for i in range(7):
        partner = E['res.partner'].create({'name': f'Khách tiềm năng {rng.choice(companies)} {i}', 'is_company': True})
        q = E['sale.order'].create({'partner_id': partner.id, 'user_id': rng.choice(sellers).id, 'order_line': [
            (0, 0, {'product_id': rng.choice(products).id, 'product_uom_qty': 1})]})
        if i % 2:
            q.action_quotation_sent()
        backdate('sale.order', q.ids, {'create_date': ago(rng.uniform(1, 35))})


# ---------------------------------------------------------------------------------------------- sales + finance
@section('Đơn bán, hóa đơn & thanh toán')
def seed_finance():
    if 'account.move' not in E:
        return
    products = E['product.product'].search([('categ_id.name', '=', 'SPDV DMAG')])
    customers = E['res.partner'].search([('is_company', '=', True), ('customer_rank', '>', 0)]) | \
        E['res.partner'].search([('is_company', '=', True), ('name', 'ilike', 'Công ty')], limit=20)
    # more confirmed orders so the revenue trend has volume
    for i in range(36):
        partner = rng.choice(customers)
        so = E['sale.order'].create({'partner_id': partner.id, 'user_id': rng.choice(sellers).id, 'order_line': [
            (0, 0, {'product_id': rng.choice(products).id, 'product_uom_qty': 1,
                    'price_unit': rng.choice([60, 90, 120, 180, 250]) * 1_000_000})]})
        so.action_confirm()
        d = ago(rng.uniform(0, 175))
        so.write({'date_order': d})
    orders = E['sale.order'].search([('state', '=', 'sale'), ('invoice_status', '=', 'to invoice')])
    for so in orders:
        try:
            inv = so._create_invoices()
        except Exception:
            continue
        inv_date = (so.date_order + timedelta(days=rng.randint(0, 4))).date()
        if inv_date > today:
            inv_date = today
        inv.write({'invoice_date': inv_date, 'invoice_payment_term_id': False})
        inv.write({'invoice_date_due': inv_date + timedelta(days=rng.choice([7, 15, 30]))})
        inv.action_post()
        due = inv.invoice_date_due
        r = rng.random()
        if due < today and r < 0.62 or due >= today and r < 0.25:
            pay_date = min(today, due + timedelta(days=rng.randint(-6, 12)))
            amount = inv.amount_total
        elif due < today and r < 0.78:
            pay_date = min(today, due + timedelta(days=rng.randint(0, 20)))
            amount = round(inv.amount_total * 0.5, -3)
        else:
            continue
        if pay_date < inv_date:
            pay_date = inv_date
        E['account.payment.register'].with_context(active_model='account.move', active_ids=inv.ids).create({
            'payment_date': pay_date, 'amount': amount})._create_payments()

    vendors = []
    for name in ('Công ty In ấn Sao Việt', 'Công ty TNHH Sự kiện Ánh Sáng', 'Công ty Phần mềm CloudOne',
                 'Công ty Quà tặng Hoa Mai', 'Văn phòng phẩm Thiên Long'):
        vendors.append(E['res.partner'].search([('name', '=', name)], limit=1) or
                       E['res.partner'].create({'name': name, 'is_company': True, 'supplier_rank': 1}))
    expense_account = E['account.account'].search([('account_type', '=', 'expense'),
                                                    ('company_id', '=', E.company.id)], limit=1)
    for i in range(16):
        bill_date = today - timedelta(days=rng.randint(5, 60))
        bill = E['account.move'].create({
            'move_type': 'in_invoice', 'partner_id': rng.choice(vendors).id, 'invoice_date': bill_date,
            'ref': f'NCC-{1000 + i}',
            'invoice_line_ids': [(0, 0, {'name': 'Chi phí dịch vụ', 'quantity': 1, 'tax_ids': [],
                                         'account_id': expense_account.id,
                                         'price_unit': rng.choice([8, 15, 25, 40, 65]) * 1_000_000})]})
        bill.write({'invoice_payment_term_id': False})
        bill.write({'invoice_date_due': today + timedelta(days=rng.randint(-15, 25))})
        bill.action_post()
        if rng.random() < 0.4 and bill.invoice_date_due < today:
            E['account.payment.register'].with_context(active_model='account.move', active_ids=bill.ids).create({
                'payment_date': bill.invoice_date_due})._create_payments()


# ---------------------------------------------------------------------------------------------- HR
@section('Nhân sự, hợp đồng, tuyển dụng, chấm công')
def seed_hr():
    if 'hr.employee' not in E:
        return
    depts = {}
    for name, size in [('Ban Giám đốc', 3), ('Kinh doanh', 8), ('Marketing', 4), ('Đào tạo & Triển khai', 9),
                       ('Tài chính – Kế toán', 3), ('Hành chính – Nhân sự', 3)]:
        depts[name] = (E['hr.department'].search([('name', '=', name)], limit=1)
                       or E['hr.department'].create({'name': name}), size)
    jobs = {}
    for name, dep, target in [('Chuyên viên Tư vấn Định vị', 'Kinh doanh', 3), ('Chuyên viên Marketing', 'Marketing', 1),
                              ('Giảng viên Đào tạo', 'Đào tạo & Triển khai', 2), ('Kế toán tổng hợp', 'Tài chính – Kế toán', 1)]:
        jobs[name] = E['hr.job'].search([('name', '=', name)], limit=1) or E['hr.job'].create({
            'name': name, 'department_id': depts[dep][0].id, 'no_of_recruitment': target})
    employees = []
    for dep_name, (dep, size) in depts.items():
        for k in range(size):
            emp = E['hr.employee'].create({'name': person(), 'department_id': dep.id,
                                           'job_title': dep_name})
            start = today - timedelta(days=rng.randint(20, 1100))
            employees.append((emp, start))
    # a few recent leavers
    leavers = rng.sample(employees, 6)
    for emp, start in employees:
        if 'hr.contract' in E:
            end = start + timedelta(days=365)
            while end < today:
                end += timedelta(days=365)
            vals = {'name': f'HĐLĐ {emp.name}', 'employee_id': emp.id, 'date_start': start, 'date_end': end,
                    'wage': rng.choice([12, 15, 18, 22, 30]) * 1_000_000, 'department_id': emp.department_id.id}
            if (today - start).days < 60:
                vals['trial_date_end'] = start + timedelta(days=60)
            contract = E['hr.contract'].create(vals)
            contract.state = 'open'
        backdate('hr.employee', emp.ids, {'create_date': fields.Datetime.to_datetime(start)})
    for emp, start in leavers:
        leave = today - timedelta(days=rng.randint(3, 170))
        if leave <= start:
            continue
        if 'hr.contract' in E:
            emp.contract_ids.write({'state': 'close', 'date_end': leave})
        emp.write({'departure_date': leave, 'active': False})

    if 'hr.applicant' in E:
        stages = E['hr.recruitment.stage'].search([], order='sequence')
        open_stages = stages.filtered(lambda s: not s.hired_stage)
        hired_stage = stages.filtered('hired_stage')[:1]
        refuse = E['hr.applicant.refuse.reason'].search([], limit=3)
        for i in range(70):
            job = rng.choice(list(jobs.values()))
            created = ago(rng.uniform(0, 150))
            app = E['hr.applicant'].create({'name': f'Ứng tuyển {job.name}', 'partner_name': person(),
                                            'job_id': job.id, 'email_from': f'uv{i}@mail.vn'})
            r = rng.random()
            if r < 0.15 and (now - created).days > 20:
                app.write({'stage_id': hired_stage.id})
                hired = created + timedelta(days=rng.uniform(12, 40))
                backdate('hr.applicant', app.ids, {'create_date': created, 'date_closed': min(hired, now),
                                                   'date_last_stage_update': min(hired, now)})
            elif r < 0.5 and (now - created).days > 10:
                app.write({'refuse_reason_id': refuse[:1].id, 'active': False})
                backdate('hr.applicant', app.ids, {'create_date': created})
            else:
                app.write({'stage_id': rng.choice(open_stages[:4]).id})
                backdate('hr.applicant', app.ids, {
                    'create_date': created,
                    'date_last_stage_update': created + timedelta(days=rng.uniform(0, max(1, (now - created).days)))})

    Line = E['account.analytic.line']
    if 'employee_id' in Line._fields and 'project_id' in Line._fields:
        projects = E['project.project'].search([('partner_id', '!=', False)], limit=6)
        delivery = [e for e, _s in employees if e.active and e.department_id.name == 'Đào tạo & Triển khai']
        for emp in delivery:
            rate = rng.choice([0.2, 0.5, 0.8, 0.95, 1.0])
            for d in range(7):
                day = today - timedelta(days=d)
                if day.weekday() >= 5 or rng.random() > rate:
                    continue
                Line.create({'name': 'Triển khai khách hàng', 'project_id': rng.choice(projects).id,
                             'employee_id': emp.id, 'date': day, 'unit_amount': rng.choice([4, 6, 7.5, 8])})


# ---------------------------------------------------------------------------------------------- events
@section('Sự kiện & hội thảo')
def seed_events():
    if 'event.event' not in E:
        return
    for name, start_in, seats in [('Hội thảo Định Vị Mới – Hà Nội', -40, 120), ('Webinar: Thương hiệu khác biệt', -12, 300),
                                  ('Workshop Vận hành bán hàng', 9, 60), ('Hội thảo Định Vị Mới – TP.HCM', 25, 150)]:
        begin = now + timedelta(days=start_in)
        # no automatic mail/badge scheduler on demo events (it would e-mail the fake attendees)
        ev = E['event.event'].create({'name': name, 'date_begin': begin, 'date_end': begin + timedelta(hours=4),
                                      'seats_limited': True, 'seats_max': seats, 'event_mail_ids': [(5, 0, 0)]})
        n = int(seats * rng.uniform(0.45, 0.9))
        regs = E['event.registration'].create([{'event_id': ev.id, 'name': person(),
                                                'email': f'kh{ev.id}_{k}@mail.vn'} for k in range(n)])
        for reg in regs:
            created = begin - timedelta(days=rng.uniform(1, 30))
            if start_in < 0:
                reg.state = 'done' if rng.random() < 0.62 else 'open'
            backdate('event.registration', reg.ids, {'create_date': min(created, now)})


# ---------------------------------------------------------------------------------------------- purchase & stock
@section('Mua hàng & kho')
def seed_purchase():
    if 'purchase.order' not in E or 'stock.picking' not in E:
        return
    vendors = E['res.partner'].search([('supplier_rank', '>', 0)]) or E['res.partner'].create(
        {'name': 'Công ty In ấn Sao Việt', 'is_company': True})
    items = []
    for name, cost in [('Bộ tài liệu Định Vị Mới (bản in)', 180_000), ('Sổ tay học viên', 45_000),
                       ('Áo đồng phục sự kiện', 120_000), ('Standee sự kiện', 650_000), ('Thẻ học viên NFC', 35_000)]:
        items.append(E['product.product'].search([('name', '=', name)], limit=1) or E['product.product'].create(
            {'name': name, 'detailed_type': 'product', 'standard_price': cost, 'list_price': cost * 2}))
    wh = E['stock.warehouse'].search([('company_id', '=', E.company.id)], limit=1)
    for i in range(26):
        approve = ago(rng.uniform(1, 120))
        po = E['purchase.order'].create({'partner_id': rng.choice(vendors).id, 'order_line': [
            (0, 0, {'product_id': p.id, 'product_qty': rng.choice([50, 100, 200, 300]), 'price_unit': p.standard_price})
            for p in rng.sample(items, rng.randint(1, 3))]})
        po.button_confirm()
        planned = approve + timedelta(days=rng.randint(3, 10))
        po.write({'date_approve': approve})
        for pick in po.picking_ids:
            pick.scheduled_date = planned
            if planned < now and rng.random() < 0.8:
                for move in pick.move_ids:
                    move.quantity = move.product_uom_qty
                pick.with_context(skip_backorder=True).button_validate()
                done = planned + timedelta(days=rng.choice([-1, 0, 0, 1, 2, 4, 7]))
                backdate('stock.picking', pick.ids, {'date_done': min(done, now), 'scheduled_date': planned,
                                                     'date_deadline': planned})
            else:
                backdate('stock.picking', pick.ids, {'scheduled_date': planned, 'date_deadline': planned})
        backdate('purchase.order', po.ids, {'date_approve': approve, 'create_date': approve})
    # outgoing deliveries (event kits sent to customers)
    customers = E['res.partner'].search([('is_company', '=', True), ('customer_rank', '>', 0)], limit=10)
    for i in range(14):
        so = E['sale.order'].create({'partner_id': rng.choice(customers).id, 'order_line': [
            (0, 0, {'product_id': rng.choice(items).id, 'product_uom_qty': rng.choice([10, 20, 30])})]})
        so.action_confirm()
        planned = ago(rng.uniform(-5, 40))
        for pick in so.picking_ids:
            if planned < now and rng.random() < 0.75:
                for move in pick.move_ids:
                    move.quantity = move.product_uom_qty
                pick.with_context(skip_backorder=True, skip_sms=True).button_validate()
                backdate('stock.picking', pick.ids, {'scheduled_date': planned, 'date_deadline': planned,
                                                     'date_done': min(now, planned + timedelta(days=rng.randint(0, 3)))})
            else:
                backdate('stock.picking', pick.ids, {'scheduled_date': planned, 'date_deadline': planned})
    for p in items:
        E['stock.warehouse.orderpoint'].create({'product_id': p.id, 'warehouse_id': wh.id,
                                                'location_id': wh.lot_stock_id.id,
                                                'product_min_qty': rng.choice([200, 400, 800]),
                                                'product_max_qty': 1200})


print('DMAG department demo data: done')
