import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [inputPath, outputPath] = process.argv.slice(2);
if (!inputPath || !outputPath) throw new Error("用法：node export_xlsx.mjs <input.json> <output.xlsx>");
const { records, summary } = JSON.parse(await fs.readFile(inputPath, "utf8"));
const workbook = Workbook.create();

function decorate(sheet, title, headers, data, widths) {
  const columns = String.fromCharCode(64 + headers.length);
  sheet.showGridLines = false;
  sheet.getRange(`A1:${columns}1`).merge();
  sheet.getRange("A1").values = [[title]];
  sheet.getRange(`A2:${columns}2`).merge();
  sheet.getRange("A2").values = [["金额单位：元；本表由资金明细汇总工具自动生成。"]];
  sheet.getRange(`A3:${columns}3`).values = [headers];
  if (data.length) sheet.getRange(`A4:${columns}${data.length + 3}`).values = data;
  sheet.getRange(`A1:${columns}1`).format = { fill: "#17365D", font: { bold: true, color: "#FFFFFF", size: 16 }, horizontalAlignment: "center", verticalAlignment: "center" };
  sheet.getRange(`A2:${columns}2`).format = { fill: "#EAF2F8", font: { color: "#506274", italic: true }, verticalAlignment: "center" };
  sheet.getRange(`A3:${columns}3`).format = { fill: "#D9EAF7", font: { bold: true, color: "#17365D" }, horizontalAlignment: "center", borders: { preset: "all", style: "thin", color: "#B8C6D5" } };
  if (data.length) sheet.getRange(`A4:${columns}${data.length + 3}`).format.borders = { preset: "inside", style: "thin", color: "#DEE6EF" };
  sheet.getRange("A1").format.rowHeight = 28;
  sheet.getRange("A2").format.rowHeight = 22;
  widths.forEach((width, index) => sheet.getRangeByIndexes(0, index, 1, 1).format.columnWidth = width);
  sheet.freezePanes.freezeRows(3);
}

const detail = workbook.worksheets.add("资金明细大表");
const detailRows = records.map((row, index) => [index + 1, row.department, new Date(`${row.happen_date}T00:00:00`), row.category, row.direction, Number(row.amount), row.description || "", row.source_file]);
decorate(detail, "资金明细大表", ["序号", "部门", "预计日期", "日报栏目", "收支方向", "金额（元）", "事项说明", "来源小表"], detailRows, [9, 16, 14, 18, 12, 16, 34, 24]);
if (detailRows.length) { detail.getRange(`C4:C${detailRows.length + 3}`).format.numberFormat = "yyyy-mm-dd"; detail.getRange(`F4:F${detailRows.length + 3}`).format.numberFormat = "#,##0.00"; }

const report = workbook.worksheets.add("日报栏目汇总");
const summaryRows = summary.rows.map(row => [row.category, Number(row.income), Number(row.expense), Number(row.net), row.count]);
decorate(report, "日报栏目汇总", ["日报栏目", "收入（元）", "支出（元）", "收支净额（元）", "明细笔数"], summaryRows, [22, 18, 18, 20, 14]);
if (summaryRows.length) report.getRange(`B4:D${summaryRows.length + 3}`).format.numberFormat = "#,##0.00";

const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(outputPath);
