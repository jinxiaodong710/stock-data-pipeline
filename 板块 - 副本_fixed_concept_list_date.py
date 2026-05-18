import tushare as ts
import pandas as pd
import duckdb
import time
from datetime import datetime

# -----------------------------------------------------------------------------
# 请在这里替换成你自己的 Tushare Pro API Token
# -----------------------------------------------------------------------------
TUSHARE_TOKEN = 'add29f4d5a76a75e6932801380bdf749ac11027e4ee98d3fe268d266'
ts.set_token(TUSHARE_TOKEN)
pro = ts.pro_api()

# --- 数据库配置 ---
DB_PATH = '/Users/jin/go/data/stock_data.duckdb'

def update_database_from_tushare(max_retries=3, delay=60):
    """
    从 Tushare API 获取最新的股票基本信息和概念板块成员，并存入 DuckDB 数据库。
    这将覆盖数据库中现有的相关表格。
    """
    print("--- 开始更新本地股票数据库 --- ")
    
    try:
        conn = duckdb.connect(DB_PATH)
        
        # --- 1. 更新股票基本信息表 (stock_basic_info) ---
        print("\n步骤 1/2: 获取所有股票基本信息和行业分类...")
        all_stocks_df = pro.stock_basic(list_status='L', fields='ts_code,symbol,name,area,industry,list_date')
        if not all_stocks_df.empty:
            # 使用 CREATE OR REPLACE TABLE 来创建或完全替换表
            conn.execute("CREATE OR REPLACE TABLE stock_basic_info AS SELECT * FROM all_stocks_df")
            print(f"成功更新 {len(all_stocks_df)} 条股票基本信息到 'stock_basic_info' 表。")
        else:
            print("警告：未能获取到股票基本信息。")

        # --- 2. 更新同花顺概念板块及成分股 (ths_concept_members) ---
        print("\n步骤 2/2: 获取所有同花顺概念板块及成分股... (此过程耗时较长，请耐心等待)")
        
        # 2.1 获取所有概念板块列表
        print("  - 正在获取概念板块列表...")
        ths_indexes = pro.ths_index(exchange='A', type='N')
        if ths_indexes.empty:
            print("错误：未能获取到同花顺概念板块列表，更新中止。")
            return
        print(f"  - 成功获取 {len(ths_indexes)} 个概念板块。")

        # 2.2 遍历获取所有成分股
        all_members = []
        for i, row in ths_indexes.iterrows():
            concept_code = row['ts_code']
            concept_name = row['name']
            retries = 0
            
            while retries < max_retries:
                try:
                    df_members = pro.ths_member(ts_code=concept_code)
                    if not df_members.empty:
                        df_members['concept_code'] = concept_code
                        df_members['concept_name'] = concept_name
                        all_members.append(df_members)
                    
                    print(f"    - 进度: {i+1}/{len(ths_indexes)} - 已处理板块: {concept_name}")
                    time.sleep(0.2) # API 频率控制
                    break
                except Exception as e:
                    retries += 1
                    print(f"    - 获取板块 {concept_name} 成分股时出错 (尝试 {retries}/{max_retries}): {e}")
                    if retries < max_retries:
                        print(f"      等待 {delay} 秒后重试...")
                        time.sleep(delay)
                    else:
                        print(f"    - 已达到最大重试次数，跳过板块 {concept_name}。")

        if all_members:
            # 2.3 合并并存入数据库
            full_concept_df = pd.concat(all_members, ignore_index=True)
            full_concept_df["concept_list_date"] = "1990-01-01"
            conn.execute("CREATE OR REPLACE TABLE ths_concept_members AS SELECT * FROM full_concept_df")
            print(f"\n成功更新 {len(full_concept_df)} 条概念成分股数据到 'ths_concept_members' 表。")
        else:
            print("警告：未能获取到任何概念板块的成分股数据。")

        print("\n--- 数据库更新完成！ ---")

    except Exception as e:
        print(f"数据库操作过程中发生错误: {e}")
    finally:
        if 'conn' in locals() and conn:
            conn.close()

def query_stock_info_from_db(ts_code: str):
    """
    从本地 DuckDB 数据库中查询指定股票的行业和概念板块信息。
    """
    if not ts_code:
        print("错误：请输入有效的股票代码。")
        return

    print(f"\n--- 正在从本地数据库查询 {ts_code} 的信息 ---")

    try:
        conn = duckdb.connect(DB_PATH, read_only=True)
        
        # --- 1. 查询基本信息和行业 ---
        try:
            basic_info_df = conn.execute("SELECT name, industry FROM stock_basic_info WHERE ts_code = ?", [ts_code]).fetchdf()
            if not basic_info_df.empty:
                stock_name = basic_info_df.loc[0, 'name']
                industry = basic_info_df.loc[0, 'industry']
                print(f"\n--- 股票基本信息 ---")
                print(f"股票代码: {ts_code}")
                print(f"股票名称: {stock_name}")
                print(f"所属行业: {industry}")
            else:
                print(f"数据库中未找到股票 {ts_code} 的基本信息。")
                print("提示：请先运行更新数据库的功能。")
                return
        except duckdb.CatalogException:
            print("错误：'stock_basic_info' 表不存在。请先运行更新数据库的功能。")
            return

        # --- 2. 查询概念板块 ---
        try:
            concepts_df = conn.execute("SELECT concept_name FROM ths_concept_members WHERE code = ?", [ts_code]).fetchdf()
            print(f"\n--- 所属概念板块 ---")
            if not concepts_df.empty:
                concept_list = concepts_df['concept_name'].tolist()
                print(f"查询完成！股票 {stock_name} ({ts_code}) 共属于以下 {len(concept_list)} 个概念板块:")
                for concept in concept_list:
                    print(f"  - {concept}")
            else:
                print(f"数据库中未找到股票 {stock_name} ({ts_code}) 所属的任何概念板块。")
        except duckdb.CatalogException:
            print("错误：'ths_concept_members' 表不存在。请先运行更新数据库的功能。")
            return

    except Exception as e:
        print(f"查询过程中发生错误: {e}")
    finally:
        if 'conn' in locals() and conn:
            conn.close()

def main():
    """
    主函数，提供用户交互界面。
    """
    while True:
        print("\n" + "="*50)
        print("  股票行业与概念板块查询工具 (数据源: DuckDB)")
        print("="*50)
        print("1. 更新本地数据库 (从Tushare获取最新数据，耗时较长)")
        print("2. 查询指定股票信息 (从本地数据库查询，速度快)")
        print("3. 退出")
        choice = input("\n请输入你的选择 (1-3): ").strip()

        if choice == '1':
            confirm = input("这将从Tushare获取大量数据并覆盖本地数据库，确认执行吗？(y/n): ").strip().lower()
            if confirm == 'y':
                update_database_from_tushare()
            else:
                print("操作已取消。")
        
        elif choice == '2':
            ts_code = input("请输入要查询的股票Tushare代码 (例如 600519.SH): ").strip()
            today_str = datetime.now().strftime('%Y%m%d')
            query_date = input(f"请输入查询日期 (格式 YYYYMMDD, 默认为 {today_str}): ").strip()
            if not query_date:
                query_date = today_str
            
            # 简单的日期格式校验
            if len(query_date) != 8 or not query_date.isdigit():
                print("日期格式错误，请输入 YYYYMMDD 格式的8位数字。")
                continue

            query_stock_info_from_db(ts_code)
        
        elif choice == '3':
            print("程序已退出。")
            break
        
        else:
            print("无效输入，请输入 1, 2 或 3。")

if __name__ == '__main__':
    main()
