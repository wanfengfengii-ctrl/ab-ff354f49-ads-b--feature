# ADS-B Position Decoder

航空监视平台的前置核对服务：在雷达融合之前，对 ADS-B 空中位置报文
（DF17, TC 9–18 / 20–22）做裁决并解出位置，避免 CPR 网格歧义、位区交界
与经度回绕把航迹投到错误半球。

每组 `frames` 可携带 **1–2 帧**：

- **两帧**：一偶一奇按全球 CPR 规则解出较新一帧的位置（与旧版完全兼容）；
- **单帧**：短时丢包只收到一帧时，必须同时提供一个同期可信参考点
  `reference`，服务按航空 CPR 本地解码规则还原位置，并以参考点为圆心做
  最短球面距离（海里）半径核对，防止网格歧义把目标投到远处。

## API

### `GET /health`

健康检查，返回 `{"status": "ok"}`。

### `POST /api/adsb/positions/decode`

接收 **1–200 组**编号唯一的报文组，组内逐项返回裁决，互不影响。

双帧请求（兼容）：

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

单帧请求（须带参考点）：

```json
{
  "pairs": [
    {
      "id": "single-001",
      "frames": [
        {"time_ms": 1759700005000, "raw": "8D40621D58C382D690C8AC2863A7"}
      ],
      "reference": {
        "lat": 52.2500,
        "lon": 3.9000,
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
  （纬度 [-90, 90]，经度 [-180, 180]）。
- `reference.time_ms`：参考点时刻，纪元毫秒；**不得晚于报文时刻，且最多
  早 30 秒**（两个边界均含）。
- `reference.maxDistanceNm`：允许半径，海里，范围 **1–500**；解出位置与
  参考点的**跨日期变更线最短球面距离**超出该值即拒绝。

合格双帧组的裁决条件（任一不满足即返回稳定错误码并标明编号）：

1. 两帧均为 CRC 校验正确的 DF17 空中位置报文；
2. 两帧 ICAO 地址一致；
3. 奇偶标志相反（一偶一奇）；
4. 接收间隔不超过 10 秒；
5. 两帧落在同一纬度带（NL 一致）。

合格单帧组的裁决条件：

1. 报文通过既有的 CRC、DF17 与空中位置类型码校验；
2. 提供参考点（缺失返回 `MISSING_REFERENCE`，为组级错误而非 422）；
3. 参考时刻不晚于报文（`REFERENCE_IN_FUTURE`）、不早于报文 30 秒
   以上（`REFERENCE_TOO_OLD`）；
4. 本地解码位置到参考点的最短球面距离不超过 `maxDistanceNm`
   （`POSITION_OUT_OF_RANGE`）。

响应（HTTP 200；每组恰好 `position` / `error` 之一）：

```json
{
  "results": [
    {
      "id": "single-001",
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
变更线（如参考点 -179.95°、报文解出 179.98°）仍按最短路径相邻处理并
落在正确一侧的网格；极区（纬度带很少时）的合法单帧同样落在参考点邻近。

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
| `REFERENCE_IN_FUTURE` | 参考点时刻晚于报文时刻 |
| `REFERENCE_TOO_OLD` | 参考点比报文早 30 秒以上 |
| `POSITION_OUT_OF_RANGE` | 解码位置超出参考点允许半径 |
| `LOCAL_DECODE_FAILED` | 参考点所处极区无法完成本地 CPR 解码 |
| `INTERNAL_ERROR` | 未预期的单组失败（不会波及其他组） |

请求级校验失败（组数超出 1–200、编号重复、帧数不在 1–2、参考点字段
越界如 `maxDistanceNm` 不在 1–500、双帧组携带 reference 等）返回 HTTP 422；
而“单帧缺参考”属于组级裁决，返回 200 下该组的稳定错误码，不遮蔽同批
其他组。

## 运行

```bash
# 构建并启动 API（宿主机端口默认 8000，可用 API_PORT 覆盖）
docker compose up --build api
API_PORT=9000 docker compose up api
```

## 一次性验证

`verify` 服务完成：镜像构建（与 api 共用同一镜像）、全部代码测试
（pytest）、以及接口冒烟——双帧回归、单帧成功、单帧越界拒绝、跨日期
变更线合法单帧与缺参考隔离——随后自行退出，并以退出码报告结果
（0 = 全部通过）：

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
  cpr.py      # 全球 CPR 解码（NL 纬度带、经度归一）、本地 CPR 解码与编码
  geo.py      # 最短球面距离（跨日期变更线经度回绕，海里）
  decoder.py  # 分组裁决：双帧 10 秒窗/较新帧；单帧参考时序与半径核对
  schemas.py  # 请求/响应模型（1–200 组、1–2 帧、参考点 1–500 NM）
  main.py     # FastAPI 入口：/health 与解码端点
tests/        # 单元与接口测试（含单帧、极区、日期变更线、边界用例）
scripts/smoke.py  # 冒烟：健康检查 + 双帧回归 + 单帧成功/越界/变更线
```
