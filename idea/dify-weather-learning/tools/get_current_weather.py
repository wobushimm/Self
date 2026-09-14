from collections.abc import Generator
from datetime import date, timedelta
import json
import re
from typing import Any

import requests

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage


WEATHER_CODES = {
    0: "晴朗", 1: "大部晴朗", 2: "局部多云", 3: "阴天", 45: "有雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "强毛毛雨", 61: "小雨", 63: "中雨",
    65: "大雨", 71: "小雪", 73: "中雪", 75: "大雪", 80: "阵雨", 81: "强阵雨",
    82: "暴雨", 95: "雷暴",
}


def parse_request(query: str) -> tuple[str, date]:
    """Extract a city and a supported date from a concise Chinese/English request."""
    text = re.sub(r"\s+", " ", query.strip())
    today = date.today()
    target_date = today

    relative_dates = {
        "大后天": 3,
        "后天": 2,
        "明天": 1,
        "tomorrow": 1,
        "today": 0,
        "今天": 0,
    }
    for word, days in relative_dates.items():
        if word in text.lower():
            target_date = today + timedelta(days=days)
            text = re.sub(re.escape(word), "", text, flags=re.IGNORECASE)
            break
    else:
        iso_match = re.search(r"\b(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})\b", text)
        chinese_match = re.search(r"(\d{1,2})月(\d{1,2})日?", text)
        try:
            if iso_match:
                target_date = date(*map(int, iso_match.groups()))
                text = text.replace(iso_match.group(0), "")
            elif chinese_match:
                month, day = map(int, chinese_match.groups())
                target_date = date(today.year, month, day)
                if target_date < today:
                    target_date = date(today.year + 1, month, day)
                text = text.replace(chinese_match.group(0), "")
        except ValueError as exc:
            raise ValueError("日期无效，请使用 YYYY-MM-DD，例如 2026-08-08。") from exc

    # Remove common request wording. What remains is sent to Open-Meteo as the place name.
    text = re.sub(
        r"(?:帮我|请|我想|想要|想知道|查询|查一下|查|看看|看一下|看|天气情况|天气预报|天气|气温|温度|湿度|怎么样|如何|的|for|weather|forecast|in)",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    city = re.sub(r"\s+", " ", text).strip(" ，,。.?？!！")
    if not city:
        raise ValueError("没有识别到地点。请例如输入“上海明天天气”。")
    return city, target_date


def clothing_advice(temp: float, rain_probability: int, wind_speed: float) -> str:
    if temp < 5:
        advice = "厚羽绒服、保暖内层和围巾"
    elif temp < 12:
        advice = "厚外套或薄羽绒服"
    elif temp < 20:
        advice = "夹克、卫衣或针织衫"
    elif temp < 28:
        advice = "短袖或薄长袖，可带一件轻外套"
    else:
        advice = "短袖和透气衣物，注意防晒补水"
    if rain_probability >= 50:
        advice += "；建议带伞或穿防水外套"
    if wind_speed >= 30:
        advice += "；风较大，外套宜防风"
    return advice


def mood_index(weather_code: int, rain_probability: int) -> tuple[int, str]:
    if weather_code in {0, 1} and rain_probability < 20:
        return 5, "非常适合外出，阳光会带来好心情。"
    if weather_code in {2, 3}:
        return 4, "心情平稳，适合安排日常活动。"
    if rain_probability >= 50 or weather_code >= 51:
        return 3, "雨天宜放慢节奏，带伞也别忘了给自己留些舒适时间。"
    return 4, "天气尚可，按计划出行即可。"


class GetCurrentWeatherTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        try:
            city, target_date = parse_request(tool_parameters["query"])
        except ValueError as exc:
            yield self.create_text_message(str(exc))
            return

        place = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": city, "count": 1, "language": "zh", "format": "json"},
            timeout=10,
        )
        place.raise_for_status()
        results = place.json().get("results", [])
        if not results:
            yield self.create_text_message(f"未找到城市：{city}")
            return

        location = results[0]
        weather = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": location["latitude"],
                "longitude": location["longitude"],
                "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m",
                "hourly": "temperature_2m,relative_humidity_2m,weather_code,precipitation_probability,wind_speed_10m",
                "start_date": target_date.isoformat(),
                "end_date": target_date.isoformat(),
                "timezone": "auto",
            },
            timeout=10,
        )
        weather.raise_for_status()
        payload = weather.json()
        hourly = payload["hourly"]
        now = payload.get("current", {}).get("time")
        start_index = 8  # Future dates: begin at 08:00 local time.
        if target_date == date.today() and now:
            start_index = min(
                next((i for i, item in enumerate(hourly["time"]) if item >= now), 23),
                23,
            )
        indices = range(start_index, min(start_index + 6, len(hourly["time"])))
        outlook = [
            {
                "time": hourly["time"][i],
                "temperature_c": hourly["temperature_2m"][i],
                "humidity_percent": hourly["relative_humidity_2m"][i],
                "condition": WEATHER_CODES.get(hourly["weather_code"][i], f"天气代码 {hourly['weather_code'][i]}"),
                "rain_probability_percent": hourly["precipitation_probability"][i],
            }
            for i in indices
        ]
        first = outlook[0]
        mood_score, mood_text = mood_index(hourly["weather_code"][start_index], first["rain_probability_percent"])
        result = {
            "request": tool_parameters["query"],
            "city": location["name"],
            "country": location.get("country"),
            "date": target_date.isoformat(),
            "summary_at_start": first,
            "next_6_hours": outlook,
            "clothing_advice": clothing_advice(
                first["temperature_c"], first["rain_probability_percent"], hourly["wind_speed_10m"][start_index]
            ),
            "mood_index": {"score_out_of_5": mood_score, "description": mood_text},
        }
        # Dify exposes tool results through separate `text` and `json` variables.
        # Emit both so a downstream LLM can consume the readable `text` variable,
        # while workflows that need structured data can still use `json`.
        yield self.create_text_message(json.dumps(result, ensure_ascii=False))
        yield self.create_json_message(result)
