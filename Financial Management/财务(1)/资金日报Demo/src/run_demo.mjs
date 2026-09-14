import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const demoDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dataDir = path.join(demoDir, "data");
const outputDir = path.join(demoDir, "output");
const inputPath = path.join(dataDir, "资金日报Demo输入.xlsx");
const outputPath = path.join(outputDir, "资金日报_demo.xlsx");

const colors = {
  navy: "#17365D",
  blue: "#D9EAF7",
  green: "#E2F0D9",
  yellow: "#FFF2CC",
  red: "#FCE4D6",
  gray: "#F2F2F2",
  line: "#D9E2F3",
};

const sampleTransactions = [
  ["DEMO-TX-001", "2025-01-02", "示例公司", "示例银行", "DEMO-ACCOUNT-001", "人民币", "收入", "客户回款", "示例回款", 1000, "示例客户", ""],
  ["DEMO-TX-002", "2025-01-02", "示例公司", "示例银行", "DEMO-ACCOUNT-001", "人民币", "支出", "工资发放", "示例工资", 500, "", ""],
  ["DEMO-TX-003", "2025-01-02", "示例公司", "示例银行", "DEMO-ACCOUNT-001", "人民币", "支出", "税费缴纳", "示例税费", 100, "示例机构", ""],
  ["DEMO-TX-004", "2025-01-02", "示例公司", "示例银行", "DEMO-ACCOUNT-001", "人民币", "支出", "内部调拨", "示例调拨", 200, "示例分支", ""],
  ["DEMO-TX-005", "2025-01-02", "示例公司", "示例银行", "DEMO-ACCOUNT-001", "人民币", "支出", "手续费", "示例手续费", 10, "示例银行", ""],
  ["DEMO-TX-006", "2025-01-02", "示例公司", "示例银行", "DEMO-ACCOUNT-001", "人民币", "收入", "其他", "待确认入账", 50, "示例对手方", ""],
  ["DEMO-TX-002", "2025-01-02", "示例公司", "示例银行", "DEMO-ACCOUNT-001", "人民币", "支出", "工资发放", "示例工资", 500, "", "重复流水示例"],
];

const mappings = [
  ["收入", "客户回款", "经营性流入", "流入", "自动", "客户回款进入经营性流入"],
  ["支出", "工资发放", "人工成本支出", "流出", "自动", "工资及奖金发放"],
  ["支出", "税费缴纳", "税费支出", "流出", "自动", "各类税费缴纳"],
  ["支出", "手续费", "手续费支出", "流出", "自动", "银行及结算手续费"],
  ["支出", "内部调拨", "内部调拨", "不计入收支", "自动", "仅保留明细，不影响收支"],
];

function asNumber(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function rowsFromSheet(sheet) {
  const used = sheet.getUsedRange(true);
  return used ? used.values : [];
}

function matrixToRecords(rows) {
  const [headers, ...data] = rows;
  return data.filter((row) => row.some((cell) => cell !== null && cell !== "")).map((row) =>
    Object.fromEntries(headers.map((header, index) => [header, row[index] ?? ""]))
  );
}

function setTitle(sheet, title, subtitle, lastColumn) {
  sheet.getRange(`A1:${lastColumn}1`).merge();
  sheet.getRange("A1").values = [[title]];
  sheet.getRange("A1").format = { fill: colors.navy, font: { bold: true, color: "#FFFFFF", size: 16 }, horizontalAlignment: "left", verticalAlignment: "center" };
  sheet.getRange("A1").format.rowHeight = 30;
  sheet.getRange(`A2:${lastColumn}2`).merge();
  sheet.getRange("A2").values = [[subtitle]];
  sheet.getRange("A2").format = { fill: "#F7FAFC", font: { color: "#5B6573", italic: true, size: 10 }, verticalAlignment: "center" };
  sheet.getRange("A2").format.rowHeight = 22;
}

function formatHeader(range) {
  range.format = { fill: colors.blue, font: { bold: true, color: colors.navy }, horizontalAlignment: "center", verticalAlignment: "center", wrapText: true, borders: { preset: "bottom", style: "thin", color: colors.line } };
  range.format.rowHeight = 25;
}

async function buildInputWorkbook() {
  const wb = Workbook.create();
  const tx = wb.worksheets.add("司库交易明细");
  const map = wb.worksheets.add("字段映射");
  const template = wb.worksheets.add("日报模板");
  const accounts = wb.worksheets.add("账户及余额");
  [tx, map, template, accounts].forEach((sheet) => { sheet.showGridLines = false; });

  setTitle(tx, "司库交易明细（模拟数据）", "Demo 假设：交易流水号为唯一标识；金额为正数，收支方向单独标识。", "L");
  const txHeaders = [["交易流水号", "交易日期", "公司", "银行", "银行账号", "币种", "交易方向", "交易类型", "摘要", "交易金额", "对手方", "备注"]];
  tx.getRange("A4:L4").values = txHeaders;
  tx.getRange(`A5:L${4 + sampleTransactions.length}`).values = sampleTransactions;
  formatHeader(tx.getRange("A4:L4"));
  tx.getRange(`B5:B${4 + sampleTransactions.length}`).format.numberFormat = "yyyy-mm-dd";
  tx.getRange(`J5:J${4 + sampleTransactions.length}`).format.numberFormat = "#,##0.00;[Red](#,##0.00);-";
  tx.getRange("A4:L11").format.borders = { preset: "insideHorizontal", style: "thin", color: "#E8EEF5" };
  tx.freezePanes.freezeRows(4);
  tx.getRange("A:A").format.columnWidth = 18;
  tx.getRange("B:B").format.columnWidth = 13;
  tx.getRange("C:E").format.columnWidth = 20;
  tx.getRange("F:I").format.columnWidth = 14;
  tx.getRange("J:J").format.columnWidth = 14;
  tx.getRange("K:L").format.columnWidth = 18;

  setTitle(map, "资金日报字段映射（模拟规则）", "财务确认后，请在此维护“交易方向 + 交易类型”到日报栏目的规则。", "F");
  map.getRange("A4:F4").values = [["交易方向", "交易类型", "日报栏目", "收支属性", "处理方式", "规则说明"]];
  map.getRange(`A5:F${4 + mappings.length}`).values = mappings;
  formatHeader(map.getRange("A4:F4"));
  map.getRange("A4:F9").format.borders = { preset: "insideHorizontal", style: "thin", color: "#E8EEF5" };
  map.freezePanes.freezeRows(4);
  map.getRange("A:F").format.columnWidth = 18;
  map.getRange("F:F").format.columnWidth = 28;

  setTitle(template, "资金日报模板（Demo 口径）", "模板展示本 Demo 的汇总栏目；正式模板由财务提供并据此替换。", "D");
  template.getRange("A4:D4").values = [["日报栏目", "收支属性", "填报方式", "说明"]];
  template.getRange("A5:D10").values = [
    ["经营性流入", "流入", "自动", "客户回款等"],
    ["人工成本支出", "流出", "自动", "工资、奖金等"],
    ["税费支出", "流出", "自动", "税费缴纳"],
    ["手续费支出", "流出", "自动", "银行手续费"],
    ["其他待补录", "待确认", "人工", "未知类型或关键字段缺失"],
    ["内部调拨", "不计入收支", "自动", "仅保留明细"],
  ];
  formatHeader(template.getRange("A4:D4"));
  template.getRange("A4:D10").format.borders = { preset: "insideHorizontal", style: "thin", color: "#E8EEF5" };
  template.getRange("A:D").format.columnWidth = 22;

  setTitle(accounts, "账户及余额（模拟数据）", "期初与司库期末余额用于日报余额校验。", "F");
  accounts.getRange("A4:F4").values = [["公司", "银行", "银行账号", "币种", "期初余额", "司库期末余额"]];
  accounts.getRange("A5:F5").values = [["示例公司", "示例银行", "DEMO-ACCOUNT-001", "人民币", 10000, 10240]];
  formatHeader(accounts.getRange("A4:F4"));
  accounts.getRange("E5:F5").format.numberFormat = "#,##0.00;[Red](#,##0.00);-";
  accounts.getRange("A:F").format.columnWidth = 22;

  await fs.mkdir(dataDir, { recursive: true });
  const out = await SpreadsheetFile.exportXlsx(wb);
  await out.save(inputPath);
}

async function generateReport() {
  const source = await FileBlob.load(inputPath);
  const input = await SpreadsheetFile.importXlsx(source);
  const transactions = matrixToRecords(rowsFromSheet(input.worksheets.getItem("司库交易明细")).slice(3));
  const mappingRows = matrixToRecords(rowsFromSheet(input.worksheets.getItem("字段映射")).slice(3));
  const account = matrixToRecords(rowsFromSheet(input.worksheets.getItem("账户及余额")).slice(3))[0];
  const mappingIndex = new Map(mappingRows.map((row) => [`${row["交易方向"]}|${row["交易类型"]}`, row]));
  const seen = new Set();
  const normalized = [];
  const exceptions = [];
  const totals = new Map();
  let inflow = 0;
  let outflow = 0;

  for (const tx of transactions) {
    const issues = [];
    const id = String(tx["交易流水号"] || "").trim();
    if (!id) issues.push("缺少交易流水号");
    if (seen.has(id)) issues.push("重复交易流水号");
    seen.add(id);
    if (!tx["摘要"]) issues.push("缺少摘要");
    const rule = mappingIndex.get(`${tx["交易方向"]}|${tx["交易类型"]}`);
    if (!rule) issues.push("未匹配日报映射规则");
    const status = issues.length ? "待补录" : "已处理";
    const column = rule ? rule["日报栏目"] : "其他待补录";
    const attribute = rule ? rule["收支属性"] : "待确认";
    const amount = asNumber(tx["交易金额"]);
    normalized.push([id, tx["交易日期"], tx["公司"], tx["银行账号"], tx["交易方向"], tx["交易类型"], tx["摘要"], amount, column, attribute, status, issues.join("；")]);
    if (issues.length) {
      exceptions.push([id, tx["交易日期"], tx["交易类型"], tx["摘要"], amount, issues.join("；"), "请财务确认分类/补录规则后重新生成"]);
      continue;
    }
    if (attribute === "流入") inflow += amount;
    if (attribute === "流出") outflow += amount;
    if (attribute !== "不计入收支") totals.set(column, (totals.get(column) || 0) + amount);
  }

  const opening = asNumber(account["期初余额"]);
  const systemClosing = asNumber(account["司库期末余额"]);
  const calculatedClosing = opening + inflow - outflow;
  const delta = calculatedClosing - systemClosing;
  const balanceStatus = Math.abs(delta) < 0.005 ? "PASS" : "FAIL";

  const wb = Workbook.create();
  const report = wb.worksheets.add("资金日报");
  const detail = wb.worksheets.add("处理后明细");
  const exception = wb.worksheets.add("异常清单");
  const ruleSnapshot = wb.worksheets.add("映射规则快照");
  [report, detail, exception, ruleSnapshot].forEach((sheet) => { sheet.showGridLines = false; });

  setTitle(report, "资金日报（Demo）", "日期：2026-08-03 ｜ 数据来源：司库交易明细 ｜ 规则版本：Demo v0.1", "F");
  report.getRange("A4:B4").values = [["基础信息", "值"]];
  report.getRange("A5:B8").values = [["公司", account["公司"]], ["银行账户", account["银行账号"]], ["币种", account["币种"]], ["处理状态", exceptions.length ? `存在 ${exceptions.length} 条待补录记录` : "全部自动处理完成"]];
  formatHeader(report.getRange("A4:B4"));
  report.getRange("A4:B8").format.borders = { preset: "insideHorizontal", style: "thin", color: "#E8EEF5" };
  report.getRange("D4:F4").values = [["余额校验", "金额", "说明"]];
  report.getRange("D5:F9").values = [
    ["期初余额", opening, "来自账户及余额表"],
    ["当日流入", inflow, "已匹配且纳入日报的流入"],
    ["当日流出", outflow, "已匹配且纳入日报的流出"],
    ["计算期末余额", calculatedClosing, "期初 + 流入 - 流出"],
    ["司库期末余额", systemClosing, `余额差异：${delta.toFixed(2)} ｜ ${balanceStatus}`],
  ];
  formatHeader(report.getRange("D4:F4"));
  report.getRange("D4:F9").format.borders = { preset: "insideHorizontal", style: "thin", color: "#E8EEF5" };
  report.getRange("E5:E9").format.numberFormat = "#,##0.00;[Red](#,##0.00);-";
  report.getRange("A11:C11").values = [["日报栏目", "收支属性", "当日金额"]];
  formatHeader(report.getRange("A11:C11"));
  const displayRows = mappingRows.filter((r) => r["收支属性"] !== "不计入收支").map((r) => [r["日报栏目"], r["收支属性"], totals.get(r["日报栏目"]) || 0]);
  displayRows.push(["其他待补录", "待确认", exceptions.reduce((sum, row) => sum + asNumber(row[4]), 0)]);
  report.getRange(`A12:C${11 + displayRows.length}`).values = displayRows;
  report.getRange(`C12:C${11 + displayRows.length}`).format.numberFormat = "#,##0.00;[Red](#,##0.00);-";
  report.getRange(`A11:C${11 + displayRows.length}`).format.borders = { preset: "insideHorizontal", style: "thin", color: "#E8EEF5" };
  report.getRange(`A${12 + displayRows.length}:C${12 + displayRows.length}`).values = [["合计（不含待补录）", "", inflow - outflow]];
  report.getRange(`A${12 + displayRows.length}:C${12 + displayRows.length}`).format = { fill: colors.green, font: { bold: true, color: colors.navy }, borders: { preset: "doubleBottom", style: "medium", color: colors.navy } };
  report.getRange(`C${12 + displayRows.length}`).format.numberFormat = "#,##0.00;[Red](#,##0.00);-";
  report.getRange("A:A").format.columnWidth = 24;
  report.getRange("B:B").format.columnWidth = 18;
  report.getRange("C:C").format.columnWidth = 16;
  report.getRange("D:D").format.columnWidth = 20;
  report.getRange("E:E").format.columnWidth = 16;
  report.getRange("F:F").format.columnWidth = 28;
  report.getRange("E9").conditionalFormats.add("cellIs", { operator: "notEqual", formula: systemClosing, format: { fill: colors.red, font: { bold: true, color: "#9C0006" } } });

  setTitle(detail, "处理后交易明细", "状态为“待补录”的记录不计入日报金额，需在异常清单中处理。", "L");
  const detailHeader = [["交易流水号", "交易日期", "公司", "银行账号", "交易方向", "交易类型", "摘要", "交易金额", "日报栏目", "收支属性", "状态", "问题说明"]];
  detail.getRange("A4:L4").values = detailHeader;
  detail.getRange(`A5:L${4 + normalized.length}`).values = normalized;
  formatHeader(detail.getRange("A4:L4"));
  detail.getRange(`B5:B${4 + normalized.length}`).format.numberFormat = "yyyy-mm-dd";
  detail.getRange(`H5:H${4 + normalized.length}`).format.numberFormat = "#,##0.00;[Red](#,##0.00);-";
  detail.getRange(`A4:L${4 + normalized.length}`).format.borders = { preset: "insideHorizontal", style: "thin", color: "#E8EEF5" };
  detail.getRange(`K5:K${4 + normalized.length}`).conditionalFormats.add("containsText", { text: "待补录", format: { fill: colors.yellow, font: { bold: true, color: "#7F6000" } } });
  detail.freezePanes.freezeRows(4);
  ["A", "C", "D", "F", "G", "I", "L"].forEach((column) => { detail.getRange(`${column}:${column}`).format.columnWidth = 19; });
  detail.getRange("B:B").format.columnWidth = 13;
  detail.getRange("E:E").format.columnWidth = 12;
  detail.getRange("H:H").format.columnWidth = 14;
  detail.getRange("J:K").format.columnWidth = 14;

  setTitle(exception, "补录与异常清单", "本表是 Demo 的人工处理出口：确认规则或补录字段后，可重新运行日报生成。", "G");
  exception.getRange("A4:G4").values = [["交易流水号", "交易日期", "交易类型", "摘要", "交易金额", "异常原因", "建议处理"]];
  const exceptionRows = exceptions.length ? exceptions : [["-", "-", "-", "-", 0, "无异常", "无需处理"]];
  exception.getRange(`A5:G${4 + exceptionRows.length}`).values = exceptionRows;
  formatHeader(exception.getRange("A4:G4"));
  exception.getRange(`E5:E${4 + exceptionRows.length}`).format.numberFormat = "#,##0.00;[Red](#,##0.00);-";
  exception.getRange(`A4:G${4 + exceptionRows.length}`).format.borders = { preset: "insideHorizontal", style: "thin", color: "#E8EEF5" };
  exception.getRange(`A5:G${4 + exceptionRows.length}`).format.fill = exceptions.length ? colors.yellow : colors.green;
  exception.getRange("A:G").format.columnWidth = 20;
  exception.getRange("F:G").format.columnWidth = 30;

  setTitle(ruleSnapshot, "映射规则快照", "本次生成所使用的规则快照；正式环境应同时记录规则版本与维护人。", "F");
  ruleSnapshot.getRange("A4:F4").values = [["交易方向", "交易类型", "日报栏目", "收支属性", "处理方式", "规则说明"]];
  ruleSnapshot.getRange(`A5:F${4 + mappingRows.length}`).values = mappingRows.map((r) => [r["交易方向"], r["交易类型"], r["日报栏目"], r["收支属性"], r["处理方式"], r["规则说明"]]);
  formatHeader(ruleSnapshot.getRange("A4:F4"));
  ruleSnapshot.getRange(`A4:F${4 + mappingRows.length}`).format.borders = { preset: "insideHorizontal", style: "thin", color: "#E8EEF5" };
  ruleSnapshot.getRange("A:F").format.columnWidth = 19;
  ruleSnapshot.getRange("F:F").format.columnWidth = 30;

  await fs.mkdir(outputDir, { recursive: true });
  const output = await SpreadsheetFile.exportXlsx(wb);
  await output.save(outputPath);
  return { wb, balanceStatus, exceptionCount: exceptions.length };
}

try {
  await fs.access(inputPath);
} catch {
  await buildInputWorkbook();
}
const { wb, balanceStatus, exceptionCount } = await generateReport();
const summary = await wb.inspect({ kind: "table", range: "资金日报!A4:F18", include: "values,formulas", tableMaxRows: 20, tableMaxCols: 8 });
const errors = await wb.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A", options: { useRegex: true, maxResults: 50 }, summary: "formula error scan" });
for (const sheetName of ["资金日报", "处理后明细", "异常清单", "映射规则快照"]) {
  const preview = await wb.render({ sheetName, autoCrop: "all", scale: 1.5, format: "png" });
  await fs.writeFile(path.join(outputDir, `${sheetName}.png`), new Uint8Array(await preview.arrayBuffer()));
}
console.log(JSON.stringify({ inputPath, outputPath, balanceStatus, exceptionCount, summary: summary.ndjson, formulaErrors: errors.ndjson }, null, 2));
