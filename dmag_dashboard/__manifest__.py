{
    'name': 'DMAG · Dashboard Điều hành',
    'version': '17.0.1.0.0',
    'category': 'Productivity',
    'summary': 'Dashboard điều hành DMAG: tiến trình học & triển khai SPDV, điểm nóng, cảnh báo trồi sụt',
    'description': """
Dashboard điều hành theo nhận diện DMAG.

* Bảng "Học & Triển khai SPDV": hành trình khách hàng từ ký hợp đồng -> học -> triển khai -> nghiệm thu
* Chỉ số so sánh kỳ trước, sparkline, điểm sức khỏe khách hàng, điểm nóng
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
