#ifndef BOARD_CFG_H
#define BOARD_CFG_H


const char board_cfg_data[] = {
  "# support in, out, in_out type\n"
  "# support i2c_port, i2s_port settings\n"
  "# support pa_gain, i2c_addr setting\n"
  "\n"
  "Board: S3_AMOLED_1_43C\n"
  "i2c: {sda: 47, scl: 48}\n"
  "i2s: {mclk: 38, bclk: 39, ws: 40, din: 42, dout: 41}\n"
  "out: {codec: ES8311, pa: 46, pa_gain: 6, use_mclk: 1}\n"
  "in: {codec: ES7210}\n"
};
const char *board_cfg_start = board_cfg_data;
const char *board_cfg_end = board_cfg_data + sizeof(board_cfg_data) - 1;  // -1去掉末尾\0

#endif
