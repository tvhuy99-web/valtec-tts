#!/usr/bin/env python3

import pathlib
import subprocess
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

# Temporary CI diagnostic: run only the Kotlin compilation task here so the
# actual compiler error is emitted in a compact form before the very large
# assembleDebug log can be truncated by the connector.
android_root = app.parent
gradlew = android_root / 'gradlew'
if not gradlew.is_file():
    raise RuntimeError(f'Missing Gradle wrapper: {gradlew}')

gradlew.chmod(gradlew.stat().st_mode | 0o111)
result = subprocess.run(
    [str(gradlew.resolve()), '--no-daemon', '--console=plain', ':app:compileDebugKotlin'],
    cwd=android_root,
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
)
if result.returncode != 0:
    lines = result.stdout.splitlines()
    needles = (
        'e: ',
        'error:',
        'FAILURE:',
        'What went wrong',
        'Execution failed for task',
        'Compilation error',
        'Unresolved reference',
        'Smart cast',
        '.kt:',
    )
    selected = [line for line in lines if any(needle in line for needle in needles)]
    print('=== Kotlin compile diagnostic ===')
    for line in selected[-120:]:
        print(line)
    print('=== Gradle tail ===')
    for line in lines[-80:]:
        print(line)
    raise RuntimeError(f'Kotlin compile diagnostic failed with exit code {result.returncode}')

print('Kotlin compile diagnostic passed')
