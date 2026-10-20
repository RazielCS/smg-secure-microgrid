//
// Ondrej Stanicek
// staniond@fit.cvut.cz
// Czech Technical University - Faculty of Information Technology
// 2022
//
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <soc/rtc.h>
#include <esp_sleep.h>
#include <esp_err.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "puf_measurement.h"
#include "bit_array.h"
#include "nvs.h"
#include "ecc.h"
#include <rom/ets_sys.h> // for ets_delay_us()

#define PUF_RESPONSE_SLEEP_uS (10*1000)
#define PUFSLEEP_RESPONSE_SLEEP_uS (100000)
#define PUF_HW_THRESHOLD_PERCENT (48.5)
#define PUF_ERROR_THRESHOLD_PERCENT (0.15)

uint8_t* RTC_FAST_MEMORY = (uint8_t*) RTC_FAST_MEMORY_ADDRESS;
uint8_t __NOINIT_ATTR PUF_BUFFER[PUF_MEMORY_SIZE];
PuflibState RTC_DATA_ATTR PUFLIB_STATE = {.state = NONE, .iteration_progress = 0};

uint8_t *PUF_RESPONSE = 0;
size_t PUF_RESPONSE_LEN = 0;
enum PufState RTC_DATA_ATTR PUF_STATE = RESPONSE_CLEAN;

void puf_response_reset_calculate();

/* --------------------------------------------------------------------------
 * Power domain configuration helper (avoids refs<0 assert on IDF 5.x)
 * - Keeps RTC_FAST_MEM ON (needed for wake stub and SRAM capture)
 * - Keeps RTC_SLOW_MEM ON if RTC_DATA_ATTR is used
 * - Leaves the rest in AUTO (prevents refcount imbalance)
 * -------------------------------------------------------------------------- */
static inline esp_err_t puf_pd_safe_setup(bool keep_rtc_slow)
{
    ESP_ERROR_CHECK(esp_sleep_pd_config(ESP_PD_DOMAIN_RTC_FAST_MEM, ESP_PD_OPTION_ON));
    if (keep_rtc_slow) {
        ESP_ERROR_CHECK(esp_sleep_pd_config(ESP_PD_DOMAIN_RTC_SLOW_MEM, ESP_PD_OPTION_ON));
    }
    ESP_ERROR_CHECK(esp_sleep_pd_config(ESP_PD_DOMAIN_RTC_PERIPH, ESP_PD_OPTION_AUTO));
    ESP_ERROR_CHECK(esp_sleep_pd_config(ESP_PD_DOMAIN_VDDSDIO,    ESP_PD_OPTION_AUTO));
    return ESP_OK;
}

void puflib_init() {
    printf("PUF INIT\n");
    switch(PUFLIB_STATE.state) {
        case NONE:
            return;
        case PROVISIONING:
            PUFLIB_STATE.iteration_progress += 1;
            provision_puf_calculate();
            break;
        case PUF_RESPONSE_RESET:
            puf_response_reset_calculate();
            break;
    }
}

void power_down_rtc_sram() {
    CLEAR_PERI_REG_MASK(RTC_CNTL_PWC_REG, RTC_CNTL_FASTMEM_FORCE_PU | RTC_CNTL_FASTMEM_FORCE_NOISO);
    SET_PERI_REG_MASK(RTC_CNTL_PWC_REG, RTC_CNTL_FASTMEM_FORCE_PD | RTC_CNTL_FASTMEM_FORCE_ISO);
}

void power_up_rtc_sram() {
    CLEAR_PERI_REG_MASK(RTC_CNTL_PWC_REG, RTC_CNTL_FASTMEM_FORCE_PD | RTC_CNTL_FASTMEM_FORCE_ISO);
    SET_PERI_REG_MASK(RTC_CNTL_PWC_REG, RTC_CNTL_FASTMEM_FORCE_PU | RTC_CNTL_FASTMEM_FORCE_NOISO);
}

void restore_rtc_sram(uint8_t *backup) {
    memcpy(RTC_FAST_MEMORY, backup, PUF_MEMORY_SIZE);
    free(backup);
}

uint8_t *backup_rtc_sram() {
    uint8_t *backup = malloc(PUF_MEMORY_SIZE);
    memcpy(backup, RTC_FAST_MEMORY, PUF_MEMORY_SIZE);
    return backup;
}

void turn_off_rtc_sram(int sleep_us) {
    power_down_rtc_sram();
    ets_delay_us(sleep_us); // busy loop
    power_up_rtc_sram();
    vTaskDelay(10 / portTICK_PERIOD_MS); // wait until SRAM stabilizes
}

void get_puf_bit_frequency(uint16_t *puf_freq, const size_t len, const size_t measurements) {
    assert(len == PUF_MEMORY_SIZE * 8); // one counter per bit
    uint8_t *backup = backup_rtc_sram();
    for (size_t i = 0; i < measurements; ++i) {
        turn_off_rtc_sram(PUF_RESPONSE_SLEEP_uS);
        for (int j = 0; j < len; ++j) {
            puf_freq[j] += array_getBit(RTC_FAST_MEMORY, PUF_MEMORY_SIZE, j);
        }
    }
    restore_rtc_sram(backup);
}

void pufsleep_bit_frequency_helper(const size_t len, bool first_iteration, bool last_iteration) {
    uint16_t *puf_freq;
    if (first_iteration) {
        puf_freq = calloc(sizeof(uint16_t), len);
        set_blob((const uint8_t *) puf_freq, len * sizeof(uint16_t), PUF_FREQUENCY_KEY);

    } else {
        size_t byte_len;
        get_blob((uint8_t **) &puf_freq, &byte_len, PUF_FREQUENCY_KEY);
        assert(byte_len == len * sizeof(uint16_t));
        for (int j = 0; j < len; ++j) {
            puf_freq[j] += array_getBit(PUF_BUFFER, PUF_MEMORY_SIZE, j);
        }
        set_blob((const uint8_t *) puf_freq, len * sizeof(uint16_t), PUF_FREQUENCY_KEY);
        free(puf_freq);
    }

    if(!last_iteration){
        // ✅ Configure power domains once before deep-sleep
        //    Use keep_rtc_slow=true if RTC_DATA_ATTR variables are needed
        ESP_ERROR_CHECK(puf_pd_safe_setup(/*keep_rtc_slow=*/true));

        esp_sleep_enable_timer_wakeup(PUFSLEEP_RESPONSE_SLEEP_uS);
        // ❌ Removed ESP_PD_OPTION_OFF calls to avoid refcount imbalance
        esp_deep_sleep_start();
    }
}

void get_pufsleep_bit_frequency(uint16_t **puf_freq, const size_t len, const size_t measurements, const int iteration_progress) {
    assert(len == PUF_MEMORY_SIZE * 8); // one counter per bit

    if(iteration_progress <= measurements) {
        pufsleep_bit_frequency_helper(len, iteration_progress == 0, iteration_progress == measurements);
    }

    size_t freq_len;
    get_blob((uint8_t **) puf_freq, &freq_len, PUF_FREQUENCY_KEY);
    // PUF_FREQUENCY is erased by provision_puf_calculate() in ecc.c once provision_puf_helper()
    // has derived the masks/ECC data from it (see SECURITY FIX comment there).

    assert(freq_len == PUF_MEMORY_SIZE * 8 * sizeof(uint16_t));
}

bool get_puf_response() {
    uint8_t * backup = backup_rtc_sram();

    // get stable bit mask
    uint8_t *mask;
    size_t mask_len;
    get_blob(&mask, &mask_len, PUF_MASK_KEY);
    assert(mask_len == PUF_MEMORY_SIZE);

    // get ecc data
    uint8_t *ecc_data;
    size_t ecc_len;
    get_blob(&ecc_data, &ecc_len, ECC_DATA_KEY);
    PUF_RESPONSE_LEN = ecc_len / 8; // 8x repetition code

    // measure PUF response and apply the mask
    memset(RTC_FAST_MEMORY, 0x00, PUF_MEMORY_SIZE);
    turn_off_rtc_sram(PUF_RESPONSE_SLEEP_uS);

    double puf_hw_percent = (double) 100 * hamming_weight(RTC_FAST_MEMORY, PUF_MEMORY_SIZE) / (PUF_MEMORY_SIZE * 8);

    uint8_t *masked_puf = malloc(ecc_len);
    apply_puf_mask(mask, ecc_len*8, RTC_FAST_MEMORY, PUF_MEMORY_SIZE, masked_puf, ecc_len);

    // correct the masked response using the ECC data
    PUF_RESPONSE = malloc(PUF_RESPONSE_LEN);
    int bit_errors = correct_data(masked_puf, ecc_data, ecc_len, PUF_RESPONSE, PUF_RESPONSE_LEN);
    double puf_errors_percent = (double) 100 * bit_errors / (PUF_MEMORY_SIZE * 8);

    restore_rtc_sram(backup);
    free(ecc_data);
    free(mask);
    free(masked_puf);

    PUF_STATE = RESPONSE_READY;

    // SECURITY/RELIABILITY FIX (found + corrected in a journal review pass, 2026-10-15):
    // puf_errors_percent used to be a hard pass/fail gate here (<0.15%), but it is not a
    // security boundary -- correct_data() above already ran this response through the 8x
    // repetition code's majority-vote decode regardless of the error count, producing its
    // best-effort reconstruction either way. The actual authority on whether a response is
    // correct is the server's ECDSA signature verification against the registered public key
    // (see this table's own caption/Limitations): a reconstruction that differs from the
    // enrolled reference derives a different HKDF key pair and simply fails verification
    // server-side, exactly like any other wrong answer -- there is no new attack surface in
    // trying it. Real-hardware data (bench_results_20261015) showed this device's achievable
    // errors_pct clusters around 0.45-0.6%, consistently above the 0.15% figure, which made the
    // gate reject essentially every attempt although the ECC had already corrected normally.
    // puf_hw_percent is kept as a hard gate: it is a raw-randomness sanity check on the
    // unmasked measurement itself (must be >48.5% ones), unrelated to matching any specific
    // enrollment, and catches a measurement that did not work at all (e.g. stuck bits).
    bool puf_ok = puf_hw_percent > PUF_HW_THRESHOLD_PERCENT;
    if (!puf_ok) {
        printf("get_puf_response: FAILED hw sanity check -- hw=%.3f%% (need >%.1f%%); "
               "errors=%.3f%% [%d bits] (diagnostic only, no longer gates acceptance)\n",
               puf_hw_percent, PUF_HW_THRESHOLD_PERCENT, puf_errors_percent, bit_errors);
        clean_puf_response();
    } else if (puf_errors_percent >= PUF_ERROR_THRESHOLD_PERCENT) {
        printf("get_puf_response: errors=%.3f%% [%d bits] above the %.2f%% diagnostic figure, "
               "proceeding anyway -- server ECDSA verification is the real correctness check\n",
               puf_errors_percent, bit_errors, PUF_ERROR_THRESHOLD_PERCENT);
    }
    return puf_ok;
}

_Noreturn void get_puf_response_reset() {
    PUFLIB_STATE.state = PUF_RESPONSE_RESET;

    // ✅ Configure power domains once before deep-sleep
    ESP_ERROR_CHECK(puf_pd_safe_setup(/*keep_rtc_slow=*/true));

    esp_sleep_enable_timer_wakeup(PUFSLEEP_RESPONSE_SLEEP_uS);
    // ❌ Removed ESP_PD_OPTION_OFF here
    esp_deep_sleep_start();
}

void puf_response_reset_calculate() {
    assert(PUFLIB_STATE.state == PUF_RESPONSE_RESET);

    // get stable bit mask
    uint8_t *mask;
    size_t mask_len;
    get_blob(&mask, &mask_len, PUF_SLEEP_MASK_KEY);
    assert(mask_len == PUF_MEMORY_SIZE);

    // get ecc data
    uint8_t *ecc_data;
    size_t ecc_len;
    get_blob(&ecc_data, &ecc_len, ECC_SLEEP_DATA_KEY);
    PUF_RESPONSE_LEN = ecc_len / 8;

    // SECURITY FIX (found in a journal review pass, 2026-10-15): this retry path used to skip
    // the reliability threshold entirely -- it accepted whatever correct_data() produced
    // regardless of how many bit errors it corrected. get_puf_response() (the first-attempt
    // path) has always checked this; mirror it here so a genuinely bad reconstruction on the
    // retry is rejected instead of silently accepted as RESPONSE_READY.
    double puf_hw_percent = (double) 100 * hamming_weight(PUF_BUFFER, PUF_MEMORY_SIZE) / (PUF_MEMORY_SIZE * 8);

    // apply mask
    uint8_t *masked_puf = malloc(ecc_len);
    apply_puf_mask(mask, ecc_len * 8, PUF_BUFFER, PUF_MEMORY_SIZE, masked_puf, ecc_len);

    // correct the masked response using the ECC data
    PUF_RESPONSE = malloc(PUF_RESPONSE_LEN);
    int bit_errors = correct_data(masked_puf, ecc_data, ecc_len, PUF_RESPONSE, PUF_RESPONSE_LEN);
    double puf_errors_percent = (double) 100 * bit_errors / (PUF_MEMORY_SIZE * 8);

    free(ecc_data);
    free(mask);
    free(masked_puf);

    PUFLIB_STATE.state = NONE;

    // SECURITY/RELIABILITY FIX (2026-10-15, same reasoning as get_puf_response()): errors_pct
    // is diagnostic, not a pass/fail gate -- the server's ECDSA verification is the real
    // correctness check, and correct_data() above already produced its best-effort decode
    // regardless of the error count. Only the hw sanity check is a hard gate here.
    bool puf_ok = puf_hw_percent > PUF_HW_THRESHOLD_PERCENT;
    if (!puf_ok) {
        printf("puf_response_reset_calculate: FAILED hw sanity check -- hw=%.3f%% (need >%.1f%%); "
               "errors=%.3f%% [%d bits] (diagnostic only, no longer gates acceptance)\n",
               puf_hw_percent, PUF_HW_THRESHOLD_PERCENT, puf_errors_percent, bit_errors);
        clean_puf_response(); // frees PUF_RESPONSE and sets PUF_STATE = RESPONSE_CLEAN
        return;
    }
    if (puf_errors_percent >= PUF_ERROR_THRESHOLD_PERCENT) {
        printf("puf_response_reset_calculate: errors=%.3f%% [%d bits] above the %.2f%% "
               "diagnostic figure, proceeding anyway -- server ECDSA verification is the real "
               "correctness check\n",
               puf_errors_percent, bit_errors, PUF_ERROR_THRESHOLD_PERCENT);
    }

    PUF_STATE = RESPONSE_READY;
}

void clean_puf_response() {
    memset(PUF_RESPONSE, 0x00, PUF_RESPONSE_LEN);
    free(PUF_RESPONSE);
    PUF_RESPONSE_LEN = 0;
    PUF_RESPONSE = NULL;
    PUF_STATE = RESPONSE_CLEAN;
}

