#!/usr/bin/env python3
'''Install the bundled sea-g2p dictionary and stamp the Android quality build.'''

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

count = text.count(old)
if count != 1:
    raise RuntimeError(f'ModelManager.modelDir anchor: expected one match, found {count}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')

gradle = app / 'build.gradle.kts'
gradle_text = gradle.read_text(encoding='utf-8')
version_old = '''        versionCode = 14
        versionName = "0.6.0-opencl-quality"
'''
version_new = '''        versionCode = 16
        versionName = "0.7.0-opencl-upstream-quality"
'''
version_count = gradle_text.count(version_old)
if version_count != 1:
    raise RuntimeError(f'Android version anchor: expected one match, found {version_count}')
gradle.write_text(gradle_text.replace(version_old, version_new, 1), encoding='utf-8')

print('Patched Android sea-g2p dictionary install and stamped upstream-quality build v16')
