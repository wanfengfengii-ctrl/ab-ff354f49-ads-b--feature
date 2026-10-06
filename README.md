# ADS-B Position Decoder

航空监视平台的前置核对服务：在雷达融合之前，对 ADS-B 空中位置报文
（DF17, TC 9–18 / 20–22）做裁决并解算位置，避免位区交界与经度回绕把航迹
投到错误半球。支持两种输入：

- **双帧组**：一偶一奇两帧，按全球 CPR 规则解出较新一帧的位置；
- **单帧组**：短时丢包只收到一帧时，凭同期可信参考点按航空 CPR 本地解码
  规则解算，并用允许半径核对，防止 CPR 网格歧义把目标投到远处。

## API

### `GET /health`

健康检查，返回 `{"status": "ok"}`。

### `POST /api/adsb/positions/decode`

接收 **1–200 组**编号唯一的报文组（每组 1–2 帧），组内逐项返回裁决，
互不影响。

双帧请求（兼容原有格式与响应）：

```json
{
  "pairs": [
    {
      "id": "pair-001",
      "frames": [
        {"time_ms": 1759700005000, "raw": "8D40621D58C382D690C8AC2863A7"},
        {"time_ms": 1759700000000, "raw": "8D40621D58C386435CC412692AD6"}
      ]
    }
  ]
}
```

单帧请求（必须提供 `reference`）：

```json
{
  "pairs": [
    {
      "id": "single-001",
      "frames": [
        {"time_ms": 1759700005000, "raw": "8D40621D58C382D690C8AC2863A7"}
      ],
      "reference": {
        "lat": 52.26,
        "lon": 3.92,
        "time_ms": 1759700002000,
        "maxDistanceNm": 25
      }
    }
  ]
}
```

- `id`：组编号，批内唯一（字符串，数字会被自动转为字符串）。
- `time_ms`：接收时刻，纪元毫秒。
- `raw`：28 位十六进制（112 比特 Mode S 帧）。
- `reference.lat` / `reference.lon`：可信参考点，十进制度
  （纬度 ±90、经度 ±180）。
- `reference.time_ms`：参考点时刻，纪元毫秒；不得晚于报文，且最多早于
  报文 30 秒（边界 30.000 秒仍接受）。
- `reference.maxDistanceNm`：允许半径，1–500 海里。本地解码结果必须落在
  以参考点为圆心的该半径内（按跨日期变更线的最短球面距离核对）。
- 双帧组可以携带 `reference`，但不参与解算，也不影响原有行为。

双帧合格组的裁决条件（任一不满足即返回稳定错误码并标明编号）：

1. 两帧均为 CRC 校验正确的 DF17 空中位置报文；
2. 两帧 ICAO 地址一致；
3. 奇偶标志相反（一偶一奇）；
4. 接收间隔不超过 10 秒；
5. 两帧落在同一纬度带（NL 一致）。

单帧组仍须通过既有的 CRC、DF17 与空中位置类型码校验，随后按航空 CPR
本地解码规则选取参考点所在的正确奇偶网格；若解算位置与参考点的最短球面
距离（跨越日期变更线时取近侧）超过 `maxDistanceNm`，则以
`POSITION_OUT_OF_RANGE` 拒绝，避免把同一 CPR 编码对应的远处网格当作目标。

响应（HTTP 200；每组恰好 `position` / `error` 之一）：

```json
{
  "results": [
    {
      "id": "pair-001",
      "status": "ok",
      "position": {
        "lat": 52.257202,
        "lon": 3.919373,
        "time_ms": 1759700005000,
        "icao": "40621D",
        "frame": "even"
      },
      "error": null
    },
    {
      "id": "pair-002",
      "status": "error",
      "position": null,
      "error": {"code": "CRC_MISMATCH", "message": "frame 0: CRC parity check failed"}
    }
  ]
}
```

位置纬度/经度为十进制度、六位小数；经度归一到 `[-180, 180)`，跨日期
变更线（如参考点 179.99°、目标网格 -179.98°）仍落在正确一侧。极区
（如纬度 85° 的偶帧）合法单帧同样落在参考点邻近的正确网格。

稳定错误码：

| 代码 | 含义 |
| --- | --- |
| `INVALID_MESSAGE` | 原文不是 28 位十六进制 |
| `CRC_MISMATCH` | CRC 校验失败 |
| `NOT_DF17` | 下行格式不是 DF17 |
| `NOT_AIRBORNE_POSITION` | 类型码不是空中位置（9–18、20–22） |
| `ICAO_MISMATCH` | 两帧 ICAO 地址不一致 |
| `SAME_CPR_FLAG` | 两帧奇偶标志相同 |
| `TIME_GAP_EXCEEDED` | 两帧相隔超过 10 秒 |
| `LATITUDE_ZONE_MISMATCH` | 两帧纬度带（NL）不一致 |
| `MISSING_REFERENCE` | 单帧组未提供参考点 |
| `REFERENCE_IN_FUTURE` | 参考时刻晚于报文时刻 |
| `REFERENCE_TOO_OLD` | 参考时刻早于报文超过 30 秒 |
| `POSITION_OUT_OF_RANGE` | 本地解码位置超出允许半径（网格歧义防护） |
| `POLAR_CPR_AMBIGUITY` | 该奇偶帧在此纬度无法解析经度（如 87° 以上奇帧） |
| `INTERNAL_ERROR` | 未预期的单组失败（不会波及其他组） |

请求级校验失败（组数超出 1–200、编号重复、帧数不在 1–2、参考点坐标或
半径越界等）返回 HTTP 422。单帧组缺少 reference 属于组内失败：HTTP 200
下返回稳定错误码 `MISSING_REFERENCE`，且不会遮蔽同批其他组。组内任何
解算失败都不会波及其他组。

## 运行

```bash
# 构建并启动 API（宿主机端口默认 8000，可用 API_PORT 覆盖）
docker compose up --build api
API_PORT=9000 docker compose up api
```

## 一次性验证

`verify` 服务完成：镜像构建（与 api 共用同一镜像）、代码测试
（pytest）、双帧回归以及单帧接口冒烟（参考点成功解码、超出半径拒绝、
参考时刻无效/过旧、日期变更线与极区合法用例），随后自行退出，
并以退出码报告结果（0 = 全部通过）：

```bash
docker compose up --build --exit-code-from verify verify
echo $?   # 0 表示测试与冒烟全部通过
```

## 本地开发

```bash
pip install -r requirements.txt
python -m pytest tests -q
uvicorn app.main:app --port 8000
python scripts/smoke.py http://localhost:8000
```

## 结构

```
app/
  adsb.py     # Mode S CRC(多项式 0x1FFF409)、DF17 帧解析、测试报文构造
  cpr.py      # 全球 CPR 解码（双帧）、本地 CPR 解码（单帧+参考点）、
              # 跨日期变更线的球面距离、经度归一与编码（测试用）
  decoder.py  # 分组裁决：双帧校验顺序/10 秒窗、单帧 30 秒参考窗与半径核对
  schemas.py  # 请求/响应模型（1–200 组、编号唯一、1–2 帧与参考点）
  main.py     # FastAPI 入口：/health 与解码端点
tests/        # 单元与接口测试（含日期变更线、纬度分区边界、单帧用例）
scripts/smoke.py  # 冒烟：健康检查 + 双帧 + 坏 CRC + 单帧成功/越界/时序 + 混合批次
```
