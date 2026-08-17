# VieNeu Android 0.9.2 validation scope

This branch adds only the following user-facing behavior on top of the validated 0.9.1 OpenCL F32 runtime:

- deterministic generation uses repetition penalty 1.2, one attempt, 96 frames per native text chunk;
- long text remains supported through the upstream chunker with `max_chars=96`;
- app-private reference WAVs persist across launches and use a versioned speaker-embedding cache;
- pronunciation selector supports the existing Northern profile and a conservative Southern d/gi-to-y onset mapping;
- Clear Speech adds punctuation pauses without changing model weights or acoustic sampling for the two normal modes.

The OpenCL F32 acoustic path and immediate real-EOS acceptance remain unchanged.
