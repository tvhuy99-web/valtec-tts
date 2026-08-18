# VieNeu Android 0.9.3

- Nút **Xóa toàn bộ nhật ký** xóa cả phiên hiện tại và các phiên cũ.
- Sau khi xóa, ứng dụng tạo phiên mới và cấu hình lại native event/stdout/stderr logging.
- Thao tác xóa chạy trên hàng đợi tổng hợp để không tranh chấp với pipeline native đang hoạt động.
- Ẩn đường dẫn model nội bộ khỏi màn hình bình thường; chi tiết tải chỉ hiện khi đang tải.
- Loại bỏ các đoạn mô tả OpenCL, hướng dẫn nhật ký và ghi chú cuối màn hình theo yêu cầu.
- Phiên bản APK: `0.9.3-log-clear-ui-cleanup` (`versionCode 20`).
