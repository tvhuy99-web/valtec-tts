#!/usr/bin/env python3
"""Fix diagnostics clearing and remove redundant UI notes for VieNeu 0.9.3."""

from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: optimize_vieneu_android_v093_ui.py <android-root>")

root = Path(sys.argv[1]).resolve()
activity = root / "app/src/main/java/com/vieneu/voiceclone/MainActivity.kt"
diagnostics = root / "app/src/main/java/com/vieneu/voiceclone/Diagnostics.kt"
layout = root / "app/src/main/res/layout/activity_main.xml"
gradle = root / "app/build.gradle.kts"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match in {path}, found {count}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    diagnostics,
    '''    fun clearOldSessions(): Int {
        ensureStarted()
        var deleted = 0
        synchronized(lock) {
            rootDir.listFiles()?.forEach { f ->
                if (f.isDirectory && f.absolutePath != sessionDir.absolutePath && f.deleteRecursively()) deleted++
            }
        }
        log("diagnostics", "sessions.cleared", data = mapOf("deleted" to deleted))
        return deleted
    }
''',
    '''    fun clearAllSessions(): Int {
        ensureStarted()
        val deletedSessions = synchronized(lock) {
            val entries = rootDir.listFiles()?.toList().orEmpty()
            val count = entries.count { it.isDirectory }
            val failed = entries.filter { it.exists() && !it.deleteRecursively() }
            if (failed.isNotEmpty()) {
                throw IllegalStateException(
                    "Không thể xóa nhật ký: " + failed.joinToString { it.name }
                )
            }

            sessionId = sessionName()
            sessionDir = File(rootDir, sessionId)
            if (!sessionDir.mkdirs() && !sessionDir.isDirectory) {
                throw IllegalStateException("Không thể tạo phiên nhật ký mới.")
            }
            appLog = File(sessionDir, "app.jsonl")
            sequence.set(0L)
            sessionStartElapsedNs = SystemClock.elapsedRealtimeNanos()
            count
        }

        log(
            category = "session",
            event = "session.start",
            data = deviceInfo(),
            snapshot = true
        )
        log(
            category = "diagnostics",
            event = "sessions.cleared",
            data = mapOf(
                "deleted_sessions" to deletedSessions,
                "new_session_id" to sessionId
            )
        )
        return deletedSessions
    }
''',
    "replace old-session pruning with complete diagnostics reset",
)

replace_once(
    activity,
    '''        findViewById<Button>(R.id.clearDiagnosticsButton).setOnClickListener {
            val deleted = Diagnostics.clearOldSessions()
            diagnosticsStatus.text = "Nhật ký hiện tại: ${Diagnostics.sessionId} · đã xóa $deleted phiên cũ"
        }
''',
    '''        val clearDiagnosticsButton = findViewById<Button>(R.id.clearDiagnosticsButton)
        clearDiagnosticsButton.setOnClickListener {
            clearDiagnosticsButton.isEnabled = false
            diagnosticsStatus.text = "Đang xóa toàn bộ nhật ký..."
            executor.execute {
                try {
                    val deleted = Diagnostics.clearAllSessions()
                    val nativeError = runCatching {
                        VieNeuNative.configureDiagnostics(
                            Diagnostics.currentSessionDir().absolutePath,
                            Diagnostics.sessionId
                        )
                    }.getOrElse {
                        it.message ?: it.javaClass.simpleName
                    }
                    runOnUiThread {
                        clearDiagnosticsButton.isEnabled = true
                        diagnosticsStatus.text = if (nativeError.isEmpty()) {
                            "Đã xóa $deleted phiên · phiên mới: ${Diagnostics.sessionId}"
                        } else {
                            "Đã xóa nhật ký app; native log lỗi: $nativeError"
                        }
                    }
                } catch (t: Throwable) {
                    Diagnostics.error("diagnostics", "sessions.clear.failure", t)
                    runOnUiThread {
                        clearDiagnosticsButton.isEnabled = true
                        diagnosticsStatus.text =
                            "Không thể xóa nhật ký: ${t.message ?: t.javaClass.simpleName}"
                    }
                }
            }
        }
''',
    "make clear-log button reset the active diagnostics session",
)

replace_once(
    activity,
    '''        downloadDetail.text = root.absolutePath
''',
    '''        downloadDetail.text = ""
        downloadDetail.visibility = View.GONE
''',
    "hide internal model directory from the normal screen",
)

replace_once(
    activity,
    '''        downloadButton.isEnabled = false
        statusText.text = "Đang tải mô hình VieNeu..."
''',
    '''        downloadButton.isEnabled = false
        downloadDetail.visibility = View.VISIBLE
        statusText.text = "Đang tải mô hình VieNeu..."
''',
    "show download detail only while downloading",
)

replace_once(
    activity,
    '''                    statusText.text = "Lỗi tải model: ${t.message ?: t.javaClass.simpleName}"
                    downloadButton.isEnabled = true
''',
    '''                    statusText.text = "Lỗi tải model: ${t.message ?: t.javaClass.simpleName}"
                    downloadDetail.visibility = View.GONE
                    downloadButton.isEnabled = true
''',
    "hide download detail after failure",
)

replace_once(
    layout,
    '''        <TextView
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="6dp"
            android:text="OpenCL F32 nhanh, cache giọng mẫu, phát âm Bắc/Nam và chế độ Rõ chữ"
            android:textSize="15sp" />

''',
    "",
    "remove runtime marketing subtitle",
)

replace_once(
    layout,
    '''        <TextView
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="6dp"
            android:text="Ghi toàn bộ vòng đời app, tải model, RAM/CPU/nhiệt, tham số tổng hợp, VieNeu native pipeline, benchmark backbone/acoustic/codec, WAV và lỗi. Khi cần phân tích tốc độ hãy xuất ZIP và gửi lại."
            android:textSize="12sp" />

''',
    "",
    "remove diagnostics explanation",
)

replace_once(
    layout,
    '''        <TextView
            android:layout_width="match_parent"
            android:layout_height="wrap_content"
            android:layout_marginTop="20dp"
            android:paddingBottom="20dp"
            android:text="Chỉ clone giọng mà bạn có quyền hoặc sự đồng ý để sử dụng. Mọi xử lý giọng mẫu và tổng hợp đều diễn ra trên thiết bị sau khi mô hình đã được tải. Nhật ký chẩn đoán có thể chứa văn bản đã nhập và tên/đường dẫn tệp cục bộ; chỉ chia sẻ ZIP khi bạn chấp nhận nội dung đó."
            android:textSize="12sp" />
''',
    "",
    "remove footer note",
)

replace_once(
    layout,
    'android:text="Xóa các phiên nhật ký cũ"',
    'android:text="Xóa toàn bộ nhật ký"',
    "rename clear-log action",
)

replace_once(
    gradle,
    '''versionCode = 19
        versionName = "0.9.2-cache-dialect-clear"''',
    '''versionCode = 20
        versionName = "0.9.3-log-clear-ui-cleanup"''',
    "bump Android 0.9.3 version",
)

required = {
    diagnostics: ("fun clearAllSessions()", "new_session_id", "sequence.set(0L)"),
    activity: (
        "Đang xóa toàn bộ nhật ký",
        "Diagnostics.clearAllSessions()",
        "VieNeuNative.configureDiagnostics(",
        "downloadDetail.visibility = View.GONE",
    ),
    layout: ("Xóa toàn bộ nhật ký",),
    gradle: ("versionCode = 20", "0.9.3-log-clear-ui-cleanup"),
}
for path, fragments in required.items():
    text = path.read_text(encoding="utf-8")
    missing = [fragment for fragment in fragments if fragment not in text]
    if missing:
        raise RuntimeError(f"{path}: missing 0.9.3 fragments {missing}")

forbidden = {
    diagnostics: ("fun clearOldSessions()",),
    activity: ("downloadDetail.text = root.absolutePath", "Diagnostics.clearOldSessions()"),
    layout: (
        "OpenCL F32 nhanh, cache giọng mẫu, phát âm Bắc/Nam và chế độ Rõ chữ",
        "Ghi toàn bộ vòng đời app, tải model, RAM/CPU/nhiệt",
        "Chỉ clone giọng mà bạn có quyền hoặc sự đồng ý để sử dụng",
    ),
}
for path, fragments in forbidden.items():
    text = path.read_text(encoding="utf-8")
    found = [fragment for fragment in fragments if fragment in text]
    if found:
        raise RuntimeError(f"{path}: stale UI/log fragments remain {found}")

print(
    "Applied Android 0.9.3: complete diagnostics reset, native log rotation, "
    "hidden internal model path and redundant-note cleanup"
)
