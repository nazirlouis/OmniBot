#include "../esp_codec_dev/include/esp_codec_dev.h"
#include "../esp_codec_dev/interface/audio_codec_ctrl_if.h"
#include "../esp_codec_dev/interface/audio_codec_gpio_if.h"

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief  Dummy codec configuration
 */
typedef struct {
    int16_t                      enable_gpio;   /*!< Enable GPIO setting */
    const audio_codec_gpio_if_t *gpio_if;       /*!< GPIO interface to control gpio */
} dummy_codec_cfg_t;

/**
 * @brief  Create a dummy codec
 *
 * @note  Dummy codec means use I2S to transfer audio data with out I2C control interface
 *
 * @param[in]  codec_cfg  Dummy codec configuration
 *
 * @return
 *       - NULL    No memory for dummy codec
 *       - Others  Dummy codec instance
 *
 */
const audio_codec_if_t *dummy_codec_new(dummy_codec_cfg_t *codec_cfg);

#ifdef __cplusplus
}
#endif
