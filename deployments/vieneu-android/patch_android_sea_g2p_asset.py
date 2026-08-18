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


gradle = app / 'build.gradle.kts'
if not gradle.is_file():
    raise RuntimeError(f'Missing Android Gradle file: {gradle}')

activity = app / 'src/main/java/com/vieneu/voiceclone/MainActivity.kt'
if not activity.is_file():
    raise RuntimeError(f'Missing generated MainActivity: {activity}')

activity_lines = activity.read_text(encoding='utf-8').splitlines()
print(f'Generated MainActivity full source ({len(activity_lines)} lines):')
for index, line in enumerate(activity_lines):
    print(f'MainActivity.kt:{index + 1}: {line}')

raise RuntimeError('Intentional diagnostic stop after dumping full generated MainActivity')
