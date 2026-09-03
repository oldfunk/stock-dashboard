-- deep_research 表清理脚本
-- 生成时间: 2026-08-31
-- 用法: sqlite3 data/db/stock_dashboard.db < scripts/drop_deep_research.sql

-- 1. 检查表是否存在
SELECT '检查 deep_research 表是否存在...' AS status;
SELECT name FROM sqlite_master WHERE type='table' AND name='deep_research';

-- 2. 如果表存在，先备份表数据到 CSV（可选）
.headers on
.mode csv
.output data/backup/deep_research_backup_2026-08-31.csv
SELECT * FROM deep_research;
.output

-- 3. 检查表内容（确认只有 5 条历史数据）
SELECT '表记录数:' AS info, COUNT(*) AS count FROM deep_research;
SELECT '股票代码:' AS info, GROUP_CONCAT(stock_code, ',') AS codes FROM deep_research;

-- 4. 删除表结构
DROP TABLE IF EXISTS deep_research;

-- 5. 验证删除
SELECT 'deep_research 表已删除' AS status;

-- 注意：此脚本执行后 deep_research 表及其数据将永久丢失
-- 请确认不再需要该表后再执行