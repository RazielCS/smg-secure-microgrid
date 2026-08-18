#include <string.h>
#include "py/runtime.h"
#include "py/obj.h"
#include "py/binary.h"
#include "mbedtls/gcm.h"

#define AESGCM_KEY_SIZE     32
#define AESGCM_BLOCK_SIZE   16
#define AESGCM_TAG_SIZE     16
#define AESGCM_NONCE_SIZE   12

typedef struct _aesgcm_obj_t {
    mp_obj_base_t base;
    uint8_t key[AESGCM_KEY_SIZE];
} aesgcm_obj_t;

static mp_obj_t aesgcm_do_encrypt(const uint8_t *key, size_t key_len,
    const uint8_t *nonce, size_t nonce_len,
    const uint8_t *plaintext, size_t pt_len,
    const uint8_t *aad, size_t aad_len,
    size_t tag_len) {

    mbedtls_gcm_context ctx;
    int ret;

    mbedtls_gcm_init(&ctx);
    ret = mbedtls_gcm_setkey(&ctx, MBEDTLS_CIPHER_ID_AES, key, key_len * 8);
    if (ret != 0) {
        mbedtls_gcm_free(&ctx);
        mp_raise_OSError(ret);
    }

    vstr_t vstr;
    vstr_init_len(&vstr, pt_len);

    uint8_t tag[AESGCM_TAG_SIZE];
    memset(tag, 0, sizeof(tag));

    ret = mbedtls_gcm_crypt_and_tag(&ctx, MBEDTLS_GCM_ENCRYPT,
        pt_len, nonce, nonce_len, aad, aad_len,
        plaintext, (uint8_t *)vstr.buf, tag_len, tag);

    mbedtls_gcm_free(&ctx);

    if (ret != 0) {
        mp_raise_OSError(ret);
    }

    mp_obj_t items[2];
    items[0] = mp_obj_new_bytes_from_vstr(&vstr);
    items[1] = mp_obj_new_bytes(tag, tag_len);
    return mp_obj_new_tuple(2, items);
}

static mp_obj_t aesgcm_do_decrypt(const uint8_t *key, size_t key_len,
    const uint8_t *nonce, size_t nonce_len,
    const uint8_t *ciphertext, size_t ct_len,
    const uint8_t *tag, size_t tag_len,
    const uint8_t *aad, size_t aad_len) {

    mbedtls_gcm_context ctx;
    int ret;

    mbedtls_gcm_init(&ctx);
    ret = mbedtls_gcm_setkey(&ctx, MBEDTLS_CIPHER_ID_AES, key, key_len * 8);
    if (ret != 0) {
        mbedtls_gcm_free(&ctx);
        mp_raise_OSError(ret);
    }

    vstr_t vstr;
    vstr_init_len(&vstr, ct_len);

    ret = mbedtls_gcm_auth_decrypt(&ctx, ct_len,
        nonce, nonce_len, aad, aad_len,
        tag, tag_len, ciphertext, (uint8_t *)vstr.buf);

    mbedtls_gcm_free(&ctx);

    if (ret != 0) {
        mp_raise_OSError(MBEDTLS_ERR_CIPHER_AUTH_FAILED);
    }

    return mp_obj_new_bytes_from_vstr(&vstr);
}

static void aesgcm_parse_aad(mp_obj_t aad_obj, mp_buffer_info_t *aad_buf) {
    aad_buf->buf = NULL;
    aad_buf->len = 0;
    if (aad_obj != mp_const_none) {
        mp_get_buffer_raise(aad_obj, aad_buf, MP_BUFFER_READ);
    }
}

static mp_obj_t aesgcm_encrypt(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum { ARG_key, ARG_nonce, ARG_data, ARG_aad, ARG_tag_len };
    static const mp_arg_t allowed_args[] = {
        { MP_QSTR_key,     MP_ARG_OBJ | MP_ARG_REQUIRED, {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_nonce,   MP_ARG_OBJ | MP_ARG_REQUIRED, {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_data,    MP_ARG_OBJ | MP_ARG_REQUIRED, {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_aad,     MP_ARG_OBJ, {.u_obj = mp_const_none} },
        { MP_QSTR_tag_len, MP_ARG_INT, {.u_int = AESGCM_TAG_SIZE} },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed_args)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed_args), allowed_args, args);

    mp_buffer_info_t key_buf, nonce_buf, data_buf, aad_buf;
    mp_get_buffer_raise(args[ARG_key].u_obj, &key_buf, MP_BUFFER_READ);
    if (key_buf.len != AESGCM_KEY_SIZE) {
        mp_raise_ValueError(MP_ERROR_TEXT("key must be 32 bytes"));
    }
    mp_get_buffer_raise(args[ARG_nonce].u_obj, &nonce_buf, MP_BUFFER_READ);
    if (nonce_buf.len < 1) {
        mp_raise_ValueError(MP_ERROR_TEXT("nonce must be at least 1 byte"));
    }
    mp_get_buffer_raise(args[ARG_data].u_obj, &data_buf, MP_BUFFER_READ);

    aesgcm_parse_aad(args[ARG_aad].u_obj, &aad_buf);

    mp_int_t tag_len = args[ARG_tag_len].u_int;
    if (tag_len < 4 || tag_len > AESGCM_TAG_SIZE) {
        mp_raise_ValueError(MP_ERROR_TEXT("tag_len must be 4-16"));
    }

    return aesgcm_do_encrypt(key_buf.buf, key_buf.len,
        nonce_buf.buf, nonce_buf.len,
        data_buf.buf, data_buf.len,
        aad_buf.buf, aad_buf.len, tag_len);
}
static MP_DEFINE_CONST_FUN_OBJ_KW(aesgcm_encrypt_obj, 3, aesgcm_encrypt);

static mp_obj_t aesgcm_decrypt(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum { ARG_key, ARG_nonce, ARG_data, ARG_tag, ARG_aad };
    static const mp_arg_t allowed_args[] = {
        { MP_QSTR_key,   MP_ARG_OBJ | MP_ARG_REQUIRED, {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_nonce, MP_ARG_OBJ | MP_ARG_REQUIRED, {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_data,  MP_ARG_OBJ | MP_ARG_REQUIRED, {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_tag,   MP_ARG_OBJ | MP_ARG_REQUIRED, {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_aad,   MP_ARG_OBJ, {.u_obj = mp_const_none} },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed_args)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed_args), allowed_args, args);

    mp_buffer_info_t key_buf, nonce_buf, data_buf, aad_buf, tag_buf;
    mp_get_buffer_raise(args[ARG_key].u_obj, &key_buf, MP_BUFFER_READ);
    if (key_buf.len != AESGCM_KEY_SIZE) {
        mp_raise_ValueError(MP_ERROR_TEXT("key must be 32 bytes"));
    }
    mp_get_buffer_raise(args[ARG_nonce].u_obj, &nonce_buf, MP_BUFFER_READ);
    if (nonce_buf.len < 1) {
        mp_raise_ValueError(MP_ERROR_TEXT("nonce must be at least 1 byte"));
    }
    mp_get_buffer_raise(args[ARG_data].u_obj, &data_buf, MP_BUFFER_READ);
    mp_get_buffer_raise(args[ARG_tag].u_obj, &tag_buf, MP_BUFFER_READ);
    if (tag_buf.len < 4 || tag_buf.len > AESGCM_TAG_SIZE) {
        mp_raise_ValueError(MP_ERROR_TEXT("tag must be 4-16 bytes"));
    }

    aesgcm_parse_aad(args[ARG_aad].u_obj, &aad_buf);

    return aesgcm_do_decrypt(key_buf.buf, key_buf.len,
        nonce_buf.buf, nonce_buf.len,
        data_buf.buf, data_buf.len,
        tag_buf.buf, tag_buf.len,
        aad_buf.buf, aad_buf.len);
}
static MP_DEFINE_CONST_FUN_OBJ_KW(aesgcm_decrypt_obj, 4, aesgcm_decrypt);

static mp_obj_t aesgcm_make_new(const mp_obj_type_t *type, size_t n_args, size_t n_kw, const mp_obj_t *args) {
    mp_arg_check_num(n_args, n_kw, 1, 1, false);

    mp_buffer_info_t key_buf;
    mp_get_buffer_raise(args[0], &key_buf, MP_BUFFER_READ);
    if (key_buf.len != AESGCM_KEY_SIZE) {
        mp_raise_ValueError(MP_ERROR_TEXT("key must be 32 bytes"));
    }

    aesgcm_obj_t *self = mp_obj_malloc(aesgcm_obj_t, type);
    memcpy(self->key, key_buf.buf, AESGCM_KEY_SIZE);
    return MP_OBJ_FROM_PTR(self);
}

static void aesgcm_print(const mp_print_t *print, mp_obj_t self_in, mp_print_kind_t kind) {
    (void)kind;
    mp_printf(print, "%q(key_size=256)", MP_QSTR_AESGCM);
}

static mp_obj_t aesgcm_obj_encrypt(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    aesgcm_obj_t *self = MP_OBJ_TO_PTR(pos_args[0]);

    enum { ARG_nonce, ARG_data, ARG_aad, ARG_tag_len };
    static const mp_arg_t allowed_args[] = {
        { MP_QSTR_nonce,   MP_ARG_OBJ | MP_ARG_REQUIRED, {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_data,    MP_ARG_OBJ | MP_ARG_REQUIRED, {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_aad,     MP_ARG_OBJ, {.u_obj = mp_const_none} },
        { MP_QSTR_tag_len, MP_ARG_INT, {.u_int = AESGCM_TAG_SIZE} },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed_args)];
    mp_arg_parse_all(n_args - 1, pos_args + 1, kw_args, MP_ARRAY_SIZE(allowed_args), allowed_args, args);

    mp_buffer_info_t nonce_buf, data_buf, aad_buf;
    mp_get_buffer_raise(args[ARG_nonce].u_obj, &nonce_buf, MP_BUFFER_READ);
    if (nonce_buf.len < 1) {
        mp_raise_ValueError(MP_ERROR_TEXT("nonce must be at least 1 byte"));
    }
    mp_get_buffer_raise(args[ARG_data].u_obj, &data_buf, MP_BUFFER_READ);

    aesgcm_parse_aad(args[ARG_aad].u_obj, &aad_buf);

    mp_int_t tag_len = args[ARG_tag_len].u_int;
    if (tag_len < 4 || tag_len > AESGCM_TAG_SIZE) {
        mp_raise_ValueError(MP_ERROR_TEXT("tag_len must be 4-16"));
    }

    return aesgcm_do_encrypt(self->key, AESGCM_KEY_SIZE,
        nonce_buf.buf, nonce_buf.len,
        data_buf.buf, data_buf.len,
        aad_buf.buf, aad_buf.len, tag_len);
}
static MP_DEFINE_CONST_FUN_OBJ_KW(aesgcm_obj_encrypt_obj, 2, aesgcm_obj_encrypt);

static mp_obj_t aesgcm_obj_decrypt(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    aesgcm_obj_t *self = MP_OBJ_TO_PTR(pos_args[0]);

    enum { ARG_nonce, ARG_data, ARG_tag, ARG_aad };
    static const mp_arg_t allowed_args[] = {
        { MP_QSTR_nonce, MP_ARG_OBJ | MP_ARG_REQUIRED, {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_data,  MP_ARG_OBJ | MP_ARG_REQUIRED, {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_tag,   MP_ARG_OBJ | MP_ARG_REQUIRED, {.u_obj = MP_OBJ_NULL} },
        { MP_QSTR_aad,   MP_ARG_OBJ, {.u_obj = mp_const_none} },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed_args)];
    mp_arg_parse_all(n_args - 1, pos_args + 1, kw_args, MP_ARRAY_SIZE(allowed_args), allowed_args, args);

    mp_buffer_info_t nonce_buf, data_buf, tag_buf, aad_buf;
    mp_get_buffer_raise(args[ARG_nonce].u_obj, &nonce_buf, MP_BUFFER_READ);
    if (nonce_buf.len < 1) {
        mp_raise_ValueError(MP_ERROR_TEXT("nonce must be at least 1 byte"));
    }
    mp_get_buffer_raise(args[ARG_data].u_obj, &data_buf, MP_BUFFER_READ);
    mp_get_buffer_raise(args[ARG_tag].u_obj, &tag_buf, MP_BUFFER_READ);
    if (tag_buf.len < 4 || tag_buf.len > AESGCM_TAG_SIZE) {
        mp_raise_ValueError(MP_ERROR_TEXT("tag must be 4-16 bytes"));
    }

    aesgcm_parse_aad(args[ARG_aad].u_obj, &aad_buf);

    return aesgcm_do_decrypt(self->key, AESGCM_KEY_SIZE,
        nonce_buf.buf, nonce_buf.len,
        data_buf.buf, data_buf.len,
        tag_buf.buf, tag_buf.len,
        aad_buf.buf, aad_buf.len);
}
static MP_DEFINE_CONST_FUN_OBJ_KW(aesgcm_obj_decrypt_obj, 3, aesgcm_obj_decrypt);

static const mp_rom_map_elem_t aesgcm_locals_dict_table[] = {
    { MP_ROM_QSTR(MP_QSTR_encrypt), MP_ROM_PTR(&aesgcm_obj_encrypt_obj) },
    { MP_ROM_QSTR(MP_QSTR_decrypt), MP_ROM_PTR(&aesgcm_obj_decrypt_obj) },
};
static MP_DEFINE_CONST_DICT(aesgcm_locals_dict, aesgcm_locals_dict_table);

MP_DEFINE_CONST_OBJ_TYPE(
    aesgcm_type,
    MP_QSTR_AESGCM,
    MP_TYPE_FLAG_NONE,
    make_new, aesgcm_make_new,
    print, aesgcm_print,
    locals_dict, &aesgcm_locals_dict
);

static const mp_rom_map_elem_t aesgcm_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__),    MP_ROM_QSTR(MP_QSTR_aesgcm) },
    { MP_ROM_QSTR(MP_QSTR_AESGCM),      MP_ROM_PTR(&aesgcm_type) },
    { MP_ROM_QSTR(MP_QSTR_encrypt),     MP_ROM_PTR(&aesgcm_encrypt_obj) },
    { MP_ROM_QSTR(MP_QSTR_decrypt),     MP_ROM_PTR(&aesgcm_decrypt_obj) },
    { MP_ROM_QSTR(MP_QSTR_BLOCK_SIZE),  MP_ROM_INT(AESGCM_BLOCK_SIZE) },
    { MP_ROM_QSTR(MP_QSTR_KEY_SIZE),    MP_ROM_INT(AESGCM_KEY_SIZE) },
    { MP_ROM_QSTR(MP_QSTR_NONCE_SIZE),  MP_ROM_INT(AESGCM_NONCE_SIZE) },
    { MP_ROM_QSTR(MP_QSTR_TAG_SIZE),    MP_ROM_INT(AESGCM_TAG_SIZE) },
};
static MP_DEFINE_CONST_DICT(aesgcm_module_globals, aesgcm_module_globals_table);

const mp_obj_module_t aesgcm_user_cmodule = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&aesgcm_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_aesgcm, aesgcm_user_cmodule);
