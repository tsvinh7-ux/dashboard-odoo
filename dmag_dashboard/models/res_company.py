from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    dmag_spdv_categ_ids = fields.Many2many(
        'product.category', 'dmag_company_spdv_categ_rel', 'company_id', 'categ_id',
        string='Nhóm sản phẩm SPDV',
        help="Đơn bán có sản phẩm thuộc các nhóm này được tính là hợp đồng SPDV. "
             "Để trống: mọi sản phẩm loại Dịch vụ.")
    dmag_stall_days = fields.Integer(
        'Ngưỡng ngưng học (ngày)', default=14,
        help="Học viên chưa hoàn thành và không có bài học nào hoàn thành trong số ngày này bị coi là ngưng học.")
    dmag_idle_days = fields.Integer(
        'Ngưỡng không hoạt động (ngày)', default=21,
        help="Khách hàng đang triển khai không có hoạt động học/triển khai trong số ngày này bị trừ điểm sức khỏe.")
    dmag_project_scope = fields.Selection([
        ('customer', 'Dự án có khách hàng'),
        ('sale', 'Dự án gắn đơn bán'),
        ('all', 'Tất cả dự án'),
    ], string='Phạm vi dự án triển khai', default='customer', required=True)
