#pragma once

// Compatibility shim for adapter sources built against a newer ggml API.
// prism keeps the per-op setters; route the generic call to them.

#include "ggml.h"

static inline bool ggml_prec_set_acc(struct ggml_tensor * a, enum ggml_prec prec) {
    switch (a->op) {
        case GGML_OP_MUL_MAT:
            ggml_mul_mat_set_prec(a, prec);
            return true;
        case GGML_OP_FLASH_ATTN_EXT:
            ggml_flash_attn_ext_set_prec(a, prec);
            return true;
        default:
            return false;
    }
}
