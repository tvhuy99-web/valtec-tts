#include <jni.h>
#include <cstdlib>

extern "C" JNIEXPORT void JNICALL
Java_com_vieneu_voiceclone_VieNeuNative_cancel(JNIEnv*, jobject) {
    ::setenv("VIENEU_V3_CANCEL_REQUESTED", "1", 1);
}
