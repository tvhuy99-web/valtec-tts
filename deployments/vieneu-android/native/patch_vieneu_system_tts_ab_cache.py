#!/usr/bin/env python3

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: patch_vieneu_system_tts_ab_cache.py <android-root>')

root = pathlib.Path(sys.argv[1]).resolve()
service = root / 'app/src/main/java/com/vieneu/voiceclone/VieNeuTtsService.kt'
layout = root / 'app/src/main/res/layout/activity_system_voice_settings.xml'

for path in (service, layout):
    if not path.is_file():
        raise RuntimeError(f'Missing A/B cache patch target: {path}')

text = service.read_text(encoding='utf-8')


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly one match, found {count}')
    text = text.replace(old, new, 1)


replace_once(
    '''        val cacheKey = pcmCacheKey(
            voice.key,
            text,
            effectiveRate,
            effectivePitch,
            settings.volume,
        )
''',
    '''        val cacheKey = pcmCacheKey(
            voice.key,
            text,
            effectiveRate,
            effectivePitch,
            settings.volume,
            settings.earlyPlayback,
        )
''',
    'A/B cache key call',
)

replace_once(
    '''    private fun pcmCacheKey(
        voiceKey: String,
        text: String,
        rate: Float,
        pitch: Float,
        volume: Float,
    ): String = buildString(voiceKey.length + text.length + 48) {
''',
    '''    private fun pcmCacheKey(
        voiceKey: String,
        text: String,
        rate: Float,
        pitch: Float,
        volume: Float,
        earlyPlayback: Boolean,
    ): String = buildString(voiceKey.length + text.length + 56) {
''',
    'A/B cache key signature',
)

replace_once(
    '''        append(volume.toBits())
        append('\\u0000')
        append(text)
''',
    '''        append(volume.toBits())
        append(':')
        append(if (earlyPlayback) 'E' else 'B')
        append('\\u0000')
        append(text)
''',
    'A/B cache namespace marker',
)

service.write_text(text, encoding='utf-8')

layout_text = layout.read_text(encoding='utf-8')
old_help = '''android:text="Tắt: đợi tạo xong rồi phát. Bật: vẫn giữ nguyên một tiêu điểm, nhưng thử phát phần PCM ổn định đầu tiên trong lúc các frame còn lại tiếp tục được tạo. Mặc định tắt để dễ so sánh chất lượng và độ trễ."'''
new_help = '''android:text="Tắt: đợi tạo xong rồi phát. Bật: vẫn giữ nguyên một tiêu điểm, nhưng thử phát phần PCM ổn định đầu tiên trong lúc các frame còn lại tiếp tục được tạo. Mặc định tắt. Khi so sánh A/B, để Tốc độ = 1.0 và Độ cao = 1.0; cache của hai chế độ được tách riêng để có thể thử cùng một câu ở cả hai chế độ."'''
count = layout_text.count(old_help)
if count != 1:
    raise RuntimeError(f'A/B help text: expected exactly one match, found {count}')
layout.write_text(layout_text.replace(old_help, new_help, 1), encoding='utf-8')

final_service = service.read_text(encoding='utf-8')
required = (
    'settings.earlyPlayback,',
    'earlyPlayback: Boolean,',
    "append(if (earlyPlayback) 'E' else 'B')",
)
missing = [fragment for fragment in required if fragment not in final_service]
if missing:
    raise RuntimeError(f'A/B cache partition contract missing: {missing}')

print('Partitioned exact PCM cache by early-playback A/B mode for fair same-text comparison')
