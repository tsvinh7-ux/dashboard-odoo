# -*- coding: utf-8 -*-
"""Sinh dữ liệu DEMO cho dashboard "Học & Triển khai SPDV".

CHỈ CHẠY TRÊN DATABASE THỬ NGHIỆM (bản sao / staging), KHÔNG chạy trên dữ liệu thật.

    ./odoo-bin shell -c odoo.conf -d <db_thu_nghiem> < dmag_dashboard/scripts/seed_demo_data.py

Tạo: nhóm sản phẩm SPDV, 4 khóa học, ~14 khách hàng doanh nghiệp, đơn bán đã xác nhận,
dự án triển khai với mốc & đầu việc, học viên với tiến độ học trải đều 6 tháng gần nhất.
"""
import random
from datetime import timedelta

from odoo import fields

rng = random.Random(17)
now = fields.Datetime.now()
cr = env.cr  # noqa: F821  (provided by odoo shell)
E = env  # noqa: F821


def ago(days, hours=0):
    return now - timedelta(days=days, hours=hours)


def backdate(model, ids, column, value):
    E.flush_all()  # pending ORM writes would otherwise overwrite the raw SQL below
    table = E[model]._table
    cr.execute(f'UPDATE "{table}" SET "{column}" = %s WHERE id = ANY(%s)', (value, list(ids)))
    E.invalidate_all()


# ------------------------------------------------------------------ products
categ = E['product.category'].search([('name', '=', 'SPDV DMAG')], limit=1) or \
    E['product.category'].create({'name': 'SPDV DMAG'})
E.company.dmag_spdv_categ_ids = [(6, 0, categ.ids)]
products = []
for name, price in [('Chương trình Định Vị Mới – Gói Chuẩn', 180_000_000),
                    ('Chương trình Định Vị Mới – Gói Nâng cao', 350_000_000),
                    ('Triển khai hệ thống vận hành Odoo', 220_000_000),
                    ('Coaching điều hành 1-1 (quý)', 90_000_000)]:
    p = E['product.product'].search([('name', '=', name)], limit=1) or E['product.product'].create({
        'name': name, 'detailed_type': 'service', 'list_price': price, 'categ_id': categ.id,
        'invoice_policy': 'order'})
    products.append(p)

# ------------------------------------------------------------------ staff
staff = []
for name, login in [('Phạm Thị Thu', 'thu.dmag'), ('Nguỵ Hữu Quang Vinh', 'vinh.dmag'),
                    ('Lê Minh Khoa', 'khoa.dmag'), ('Trần Bảo Ngọc', 'ngoc.dmag')]:
    u = E['res.users'].search([('login', '=', login)], limit=1) or E['res.users'].create({
        'name': name, 'login': login,
        'groups_id': [(6, 0, [E.ref('project.group_project_user').id, E.ref('sales_team.group_sale_salesman').id,
                              E.ref('website_slides.group_website_slides_officer').id])]})
    staff.append(u)

# ------------------------------------------------------------------ courses
course_specs = [('Định vị thương hiệu 101', 10), ('Chiến lược khác biệt hóa', 12),
                ('Vận hành bán hàng chuẩn DMAG', 8), ('Số hóa doanh nghiệp với Odoo', 14)]
courses = []
for i, (name, n) in enumerate(course_specs):
    ch = E['slide.channel'].search([('name', '=', name)], limit=1)
    if not ch:
        ch = E['slide.channel'].create({'name': name, 'user_id': staff[i % len(staff)].id, 'enroll': 'invite',
                                        'visibility': 'members', 'is_published': True})
        E['slide.slide'].create([{'name': f'Bài {k + 1}: {name}', 'channel_id': ch.id, 'slide_category': 'article',
                                  'html_content': '<p>Nội dung</p>', 'is_published': True,
                                  'completion_time': 0.5} for k in range(n)])
    courses.append(ch)

# ------------------------------------------------------------------ customers
company_names = ['Công ty TNHH Nha khoa Kim', 'Hộ kinh doanh Phòng khám Mắt Sáng', 'Công ty CP Thực phẩm Xanh',
                 'Công ty TNHH C&N', 'Công ty TNHH Dịch vụ Y tế An Tâm', 'Công ty CP Nội thất Mộc Việt',
                 'Công ty TNHH Giáo dục Ánh Dương', 'Chuỗi Spa Hoa Sen', 'Công ty CP Xây dựng Hưng Phát',
                 'Công ty TNHH Logistics Bắc Nam', 'Phòng khám Đa khoa Hòa Bình', 'Công ty CP Dược Minh Châu',
                 'Công ty TNHH Thời trang Lụa', 'Công ty CP Công nghệ Sao Mai']
first = ['Anh', 'Bình', 'Chi', 'Dũng', 'Hà', 'Hùng', 'Lan', 'Linh', 'Minh', 'Nam', 'Ngân', 'Phúc', 'Quân', 'Trang', 'Tú', 'Vy']
last = ['Nguyễn', 'Trần', 'Lê', 'Phạm', 'Hoàng', 'Vũ', 'Đặng', 'Bùi']

# Profile: (days since contract, learning pace 0..1, deploy discipline 0..1, kicked-off?)
profiles = [(170, .95, .9, True), (150, .4, .5, True), (130, .9, .85, True), (120, .2, .3, True),
            (100, .8, .9, True), (85, .6, .4, True), (70, .9, .95, True), (60, .3, .6, True),
            (45, .85, .8, True), (35, .5, .5, True), (25, .7, .9, True), (18, .6, 1, True),
            (9, 0, 1, False), (4, 0, 1, False)]

for idx, cname in enumerate(company_names):
    if E['res.partner'].search_count([('name', '=', cname), ('is_company', '=', True)]):
        continue
    age, pace, discipline, kicked = profiles[idx]
    sales = staff[idx % 2]
    pm = staff[2 + idx % 2]
    comp = E['res.partner'].create({'name': cname, 'is_company': True, 'user_id': sales.id,
                                    'phone': f'09{rng.randint(10000000, 99999999)}',
                                    'email': f'lienhe{idx}@khachhang.vn'})
    contacts = E['res.partner'].create([{
        'name': f'{rng.choice(last)} {rng.choice(first)}', 'parent_id': comp.id, 'type': 'contact',
        'email': f'hv{idx}_{k}@khachhang.vn'} for k in range(rng.randint(2, 4))])

    # contract
    product = products[idx % 2] if idx % 3 else products[2]
    lines = [(0, 0, {'product_id': product.id, 'product_uom_qty': 1})]
    if idx % 4 == 0:
        lines.append((0, 0, {'product_id': products[3].id, 'product_uom_qty': 1}))
    so = E['sale.order'].create({'partner_id': comp.id, 'user_id': sales.id, 'order_line': lines})
    so.action_confirm()
    signed = ago(age, rng.randint(1, 8))
    so.write({'date_order': signed})
    backdate('sale.order', so.ids, 'create_date', signed)

    # learning
    my_courses = courses[:2] if idx % 2 else [courses[0], courses[2]]
    if idx % 5 == 0:
        my_courses = my_courses + [courses[3]]
    if age > 7:
        for ch in my_courses:
            ch.sudo()._action_add_members(contacts)
            enrolls = E['slide.channel.partner'].search([('channel_id', '=', ch.id), ('partner_id', 'in', contacts.ids)])
            enrolled_at = signed + timedelta(days=rng.randint(2, 7))
            backdate('slide.channel.partner', enrolls.ids, 'create_date', enrolled_at)
            slides = ch.slide_content_ids.sorted('sequence')
            for contact in contacts:
                learner_pace = max(0.0, min(1.0, pace + rng.uniform(-.25, .2)))
                elapsed = (now - enrolled_at).days
                n_done = min(len(slides), int(len(slides) * learner_pace * min(1, elapsed / 60.0) + .5))
                if n_done == 0:
                    continue
                # weak learners stop early -> stalled
                stop_at = elapsed if learner_pace > .55 else int(elapsed * rng.uniform(.2, .6))
                day_marks = sorted(rng.uniform(0, max(1, stop_at)) for _ in range(n_done))
                for slide, d in zip(slides[:n_done], day_marks):
                    rec = E['slide.slide.partner'].create({'slide_id': slide.id, 'partner_id': contact.id,
                                                          'completed': True})
                    backdate('slide.slide.partner', rec.ids, 'write_date', enrolled_at + timedelta(days=d))
            E['slide.channel.partner'].invalidate_model()
            for e in enrolls:
                if e.member_status == 'completed':
                    last_done = max(E['slide.slide.partner'].search([
                        ('channel_id', '=', ch.id), ('partner_id', '=', e.partner_id.id)]).mapped('write_date'))
                    backdate('slide.channel.partner', e.ids, 'dmag_completed_date', last_done)

    # deployment project
    if not kicked:
        continue
    kickoff = signed + timedelta(days=rng.randint(3, 10))
    project = E['project.project'].create({
        'name': f'Triển khai {product.name.split("–")[-1].strip()} · {cname}',
        'partner_id': comp.id, 'user_id': pm.id, 'allow_milestones': True,
        'sale_line_id': so.order_line[:1].id, 'date_start': kickoff.date()})
    backdate('project.project', project.ids, 'create_date', kickoff)
    phases = ['Khảo sát & chẩn đoán', 'Thiết kế định vị', 'Đào tạo đội ngũ', 'Triển khai vận hành', 'Nghiệm thu']
    duration = 150
    for p_i, phase in enumerate(phases):
        ms_deadline = (kickoff + timedelta(days=duration * (p_i + 1) / len(phases))).date()
        ms = E['project.milestone'].create({'name': phase, 'project_id': project.id, 'deadline': ms_deadline})
        for t_i in range(rng.randint(3, 5)):
            created = kickoff + timedelta(days=duration * p_i / len(phases) + rng.uniform(0, 8))
            if created > now:
                continue
            deadline = created + timedelta(days=rng.randint(7, 25))
            task = E['project.task'].create({
                'name': f'{phase} – hạng mục {t_i + 1}', 'project_id': project.id, 'milestone_id': ms.id,
                'user_ids': [(6, 0, [rng.choice(staff[2:]).id])] if rng.random() > .08 else [],
                'date_deadline': deadline})
            backdate('project.task', task.ids, 'create_date', created)
            # done? depends on discipline and time
            finish = deadline + timedelta(days=rng.uniform(-6, 4) + (1 - discipline) * rng.uniform(0, 20))
            if finish < now and rng.random() < .55 + .45 * discipline:
                task.state = '1_done'
                backdate('project.task', task.ids, 'dmag_closed_date', max(created + timedelta(hours=4), finish))
        if all(t.state == '1_done' for t in ms.task_ids) and ms.task_ids:
            ms.is_reached = True
    status = 'on_track' if discipline >= .8 else 'at_risk' if discipline >= .5 else 'off_track'
    E['project.update'].create({'name': 'Cập nhật tuần', 'project_id': project.id, 'status': status,
                                'user_id': pm.id})

cr.commit()
print('DMAG demo data: done')
