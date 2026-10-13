{
    'name': 'DMAG · Dashboard Điều hành',
    'version': '17.0.2.0.0',
    'category': 'Productivity',
    'summary': 'Bộ dashboard điều hành DMAG theo vai trò: Tổng quan, Kinh doanh, Marketing, Học & Triển khai, Tài chính, Nhân sự, Mua hàng & Kho',
    'description': """
Dashboard điều hành theo nhận diện DMAG.

* 7 bảng theo vai trò: Tổng quan điều hành, Kinh doanh, Marketing, Học & Triển khai SPDV,
  Tài chính, Nhân sự, Mua hàng & Kho. Bảng nào thiếu ứng dụng nguồn sẽ tự ẩn dữ liệu.
* Chỉ số so sánh kỳ trước, sparkline, điểm nóng, phễu, tuổi nợ, xếp hạng
* Drill-down 3 tầng: chỉ số -> danh sách / hồ sơ 360 khách hàng, khóa học -> bản ghi gốc
* Quy tắc cảnh báo trồi sụt (kiểu Google Analytics Insights), gửi Discuss + thông báo tức thời
    """,
    'author': 'DMAG',
    'license': 'LGPL-3',
    'depends': ['sale_project', 'website_slides', 'mail'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'data/alert_data.xml',
        'views/alert_views.xml',
        'views/res_config_settings_views.xml',
        'views/menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'dmag_dashboard/static/src/**/*.scss',
            'dmag_dashboard/static/src/**/*.js',
            'dmag_dashboard/static/src/**/*.xml',
        ],
    },
    'application': True,
    'installable': True,
}
