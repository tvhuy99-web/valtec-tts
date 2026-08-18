#!/usr/bin/env python3

import pathlib
import sys

if len(sys.argv) != 2:
    raise SystemExit('usage: patch_android_sea_g2p_asset.py <android-app-dir>')

app = pathlib.Path(sys.argv[1])
path = app / 'src/main/java/com/vieneu/voiceclone/ModelManager.kt'
text = path.read_text(encoding='utf-8')

old = '''    fun modelDir(context: Context): File {
        val base = context.getExternalFilesDir(null) ?: context.filesDir
        return File(base, "vieneu-v3-turbo-native")
    }
'''
new = '''    fun modelDir(context: Context): File {
        val base = context.getExternalFilesDir(null) ?: context.filesDir
        val root = File(base, "vieneu-v3-turbo-native")
        root.mkdirs()
        ensureBundledSeaG2pDictionary(context, root)
        return root
    }

    private fun ensureBundledSeaG2pDictionary(context: Context, root: File) {
        val target = File(root, "sea_g2p.bin")
        if (target.isFile && target.length() > 0L) return

        val temporary = File(root, "sea_g2p.bin.part")
        context.assets.open("sea_g2p.bin").use { input ->
            temporary.outputStream().buffered().use { output -> input.copyTo(output) }
        }
        if (target.exists()) target.delete()
        if (!temporary.renameTo(target)) {
            temporary.copyTo(target, overwrite = true)
            temporary.delete()
        }
        check(target.isFile && target.length() > 0L) {
            "Không thể cài từ điển phát âm tiếng Việt sea-g2p."
        }
    }
'''

if new in text:
    print('Android sea-g2p dictionary install is already patched')
else:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f'ModelManager.modelDir anchor: expected one match, found {count}'
        )
    path.write_text(text.replace(old, new, 1), encoding='utf-8')
    print('Patched Android sea-g2p dictionary install')

# optimize_vieneu_android_cache_v4.py builds a Kotlin block inside a Python
# triple-quoted string. Its newline marker is currently materialized as a real
# line break between Kotlin quotes. Repair that generated source before Gradle.
activity = app / 'src/main/java/com/vieneu/voiceclone/MainActivity.kt'
activity_text = activity.read_text(encoding='utf-8')
broken_newline_literal = '        temp.writeText(hash + "\n", Charsets.UTF_8)'
fixed_newline_literal = '        temp.writeText(hash + "\\n", Charsets.UTF_8)'

if broken_newline_literal in activity_text:
    activity_text = activity_text.replace(
        broken_newline_literal,
        fixed_newline_literal,
        1,
    )
    activity.write_text(activity_text, encoding='utf-8')
    print('Repaired generated MainActivity Kotlin newline literal')
elif fixed_newline_literal in activity_text:
    print('Generated MainActivity Kotlin newline literal is already valid')
else:
    raise RuntimeError('MainActivity newline-literal anchor was not found')

gradle = app / 'build.gradle.kts'
if not gradle.is_file():
    raise RuntimeError(f'Missing Android Gradle file: {gradle}')
