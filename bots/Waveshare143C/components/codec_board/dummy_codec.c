
#include "dummy_codec.h"

typedef struct {
    audio_codec_if_t             base;
    const audio_codec_gpio_if_t *gpio_if;
    bool                         is_open;
    bool                         enable;
} dummy_codec_t;

typedef struct {
    audio_codec_ctrl_if_t base;
    bool                  is_open;
} dummy_codec_ctrl_t;

static int dummy_codec_open(const audio_codec_if_t *h, void *cfg, int cfg_size)
{
    dummy_codec_cfg_t *codec_cfg = (dummy_codec_cfg_t *)cfg;
    if (cfg_size != sizeof(dummy_codec_cfg_t) || codec_cfg->gpio_if == NULL) {
        return -1;
    }
    dummy_codec_t *codec = (dummy_codec_t *)h;
    codec->gpio_if = codec_cfg->gpio_if;
    codec->gpio_if->setup(codec_cfg->enable_gpio, AUDIO_GPIO_DIR_OUT, AUDIO_GPIO_MODE_FLOAT);
    codec->gpio_if->set(codec_cfg->enable_gpio, true);
    codec->is_open = true;
    return 0;
}

static bool dummy_codec_is_open(const audio_codec_if_t *h)
{
    dummy_codec_t *codec = (dummy_codec_t *)h;
    return codec->is_open;
}

static int dummy_codec_enable(const audio_codec_if_t *h, bool enable)
{
    dummy_codec_t *codec = (dummy_codec_t *)h;
    codec->enable = enable;
    return 0;
}

static int dummy_codec_set_fs(const audio_codec_if_t *h, esp_codec_dev_sample_info_t *fs)
{
    return 0;
}

static int dummy_codec_close(const audio_codec_if_t *h)
{
    dummy_codec_t *codec = (dummy_codec_t *)h;
    // Auto disable when codec closed
    if (codec->enable) {
        dummy_codec_enable(h, false);
    }
    codec->is_open = false;
    return 0;
}

const audio_codec_if_t *dummy_codec_new(dummy_codec_cfg_t *codec_cfg)
{
    dummy_codec_t *codec = (dummy_codec_t *)calloc(1, sizeof(dummy_codec_t));
    if (codec == NULL) {
        return NULL;
    }
    codec->base.open = dummy_codec_open;
    codec->base.is_open = dummy_codec_is_open;
    codec->base.enable = dummy_codec_enable;
    codec->base.set_fs = dummy_codec_set_fs;
    codec->base.close = dummy_codec_close;
    codec->base.open(&codec->base, codec_cfg, sizeof(dummy_codec_cfg_t));
    return &codec->base;
}
