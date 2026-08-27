"""
增强版AI分析器 - 实施模板

这个模板可以直接复制到你的项目中，用于提升AI自动化任务的思考深度。
"""

import json
import logging
import os
import re
import time
from typing import Optional, Dict, List, Any
from datetime import datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

class BusinessLogicAnalyzer:
    """业务逻辑深度分析器"""
    
    def analyze_investment_thinking(self, stock_data, market_context=None):
        """分析投资逻辑的深度"""
        
        market_context = market_context or {}
        
        investment_thinking_analysis = {
            "business_model_understanding": self._analyze_business_model(stock_data),
            "competitive_advantage": self._analyze_competitive_advantage(stock_data),
            "growth_potential": self._analyze_growth_potential(stock_data, market_context),
            "risk_assessment": self._analyze_risk_factors(stock_data, market_context),
            "valuation_logic": self._analyze_valuation_logic(stock_data),
            "investment_thesis": self._formulate_investment_thesis(stock_data, market_context)
        }
        
        return investment_thinking_analysis
    
    def _analyze_business_model(self, stock_data):
        """分析商业模式"""
        business_model = {
            "revenue_model": self._analyze_revenue_model(stock_data),
            "cost_structure": self._analyze_cost_structure(stock_data),
            "profitability_drivers": self._analyze_profitability_drivers(stock_data),
            "scalability_potential": self._analyze_scalability(stock_data)
        }
        return business_model
    
    def _analyze_revenue_model(self, stock_data):
        """分析收入模式"""
        revenue_growth = stock_data.get('revenue_growth', 0)
        revenue_model = {
            "growth_rate": revenue_growth,
            "model_type": self._determine_revenue_model_type(stock_data),
            "diversification": self._assess_revenue_diversification(stock_data),
            "sustainability": self._assess_revenue_sustainability(revenue_growth)
        }
        return revenue_model
    
    def _analyze_cost_structure(self, stock_data):
        """分析成本结构"""
        gross_margin = stock_data.get('gross_margin', 0)
        cost_structure = {
            "gross_margin": gross_margin,
            "operational_efficiency": self._assess_operational_efficiency(gross_margin),
            "cost_control": self._assess_cost_control(stock_data),
            "economies_of_scale": self._assess_economies_of_scale(stock_data)
        }
        return cost_structure
    
    def _analyze_profitability_drivers(self, stock_data):
        """分析盈利驱动因素"""
        roe = stock_data.get('roe', 0)
        net_margin = stock_data.get('net_margin', 0)
        profitability_drivers = {
            "roe": roe,
            "net_margin": net_margin,
            "asset_turnover": self._calculate_asset_turnover(stock_data),
            "financial_leverage": self._assess_financial_leverage(stock_data),
            "key_drivers": self._identify_profitability_drivers(stock_data)
        }
        return profitability_drivers
    
    def _analyze_scalability(self, stock_data):
        """分析可扩展性"""
        scalability = {
            "scalability_potential": self._assess_scalability_potential(stock_data),
            "capital_intensity": self._assess_capital_intensity(stock_data),
            "operational_leverage": self._assess_operational_leverage(stock_data),
            "growth_capacity": self._assess_growth_capacity(stock_data)
        }
        return scalability
    
    def _analyze_competitive_advantage(self, stock_data):
        """分析竞争优势"""
        competitive_advantage = {
            "moat_strength": self._assess_moat_strength(stock_data),
            "competitive_position": self._assess_competitive_position(stock_data),
            "sustainability": self._assess_sustainability(stock_data),
            "barriers_to_entry": self._assess_barriers_to_entry(stock_data)
        }
        return competitive_advantage
    
    def _analyze_growth_potential(self, stock_data, market_context):
        """分析增长潜力"""
        growth_potential = {
            "organic_growth": self._analyze_organic_growth(stock_data),
            "mergers_acquisitions": self._analyze_ma_potential(stock_data),
            "market_expansion": self._analyze_market_expansion(stock_data, market_context),
            "innovation_capacity": self._analyze_innovation_capacity(stock_data)
        }
        return growth_potential
    
    def _analyze_risk_factors(self, stock_data, market_context):
        """分析风险因素"""
        risk_factors = {
            "market_risks": self._analyze_market_risks(stock_data, market_context),
            "operational_risks": self._analyze_operational_risks(stock_data),
            "financial_risks": self._analyze_financial_risks(stock_data),
            "strategic_risks": self._analyze_strategic_risks(stock_data)
        }
        return risk_factors
    
    def _analyze_valuation_logic(self, stock_data):
        """分析估值逻辑"""
        valuation_logic = {
            "valuation_method": self._determine_valuation_method(stock_data),
            "valuation_multiples": self._analyze_valuation_multiples(stock_data),
            "discount_rate": self._determine_discount_rate(stock_data),
            "terminal_growth": self._determine_terminal_growth(stock_data)
        }
        return valuation_logic
    
    def _formulate_investment_thesis(self, stock_data, market_context):
        """制定投资逻辑"""
        investment_thesis = {
            "investment_case": self._formulate_investment_case(stock_data, market_context),
            "time_horizon": self._determine_time_horizon(stock_data),
            "expected_return": self._estimate_expected_return(stock_data),
            "risk_return_profile": self._assess_risk_return_profile(stock_data)
        }
        return investment_thesis
    
    # 辅助方法
    def _determine_revenue_model_type(self, stock_data):
        """确定收入模式类型"""
        revenue_growth = stock_data.get('revenue_growth', 0)
        if revenue_growth > 20:
            return "高增长型"
        elif revenue_growth > 10:
            return "稳健增长型"
        elif revenue_growth > 0:
            return "稳定型"
        else:
            return "萎缩型"
    
    def _assess_revenue_diversification(self, stock_data):
        """评估收入多元化"""
        # 简化的多元化评估
        sector = stock_data.get('sector', '未知')
        if sector in ['科技', '医药', '消费']:
            return "较高"
        else:
            return "中等"
    
    def _assess_revenue_sustainability(self, revenue_growth):
        """评估收入可持续性"""
        if revenue_growth > 15:
            return "高"
        elif revenue_growth > 5:
            return "中"
        else:
            return "低"
    
    def _assess_operational_efficiency(self, gross_margin):
        """评估运营效率"""
        if gross_margin > 30:
            return "高"
        elif gross_margin > 20:
            return "中"
        else:
            return "低"
    
    def _assess_cost_control(self, stock_data):
        """评估成本控制"""
        # 简化的成本控制评估
        return "中等"
    
    def _assess_economies_of_scale(self, stock_data):
        """评估规模经济"""
        market_cap = stock_data.get('market_cap', 0)
        if market_cap > 1000:
            return "高"
        elif market_cap > 100:
            return "中"
        else:
            return "低"
    
    def _calculate_asset_turnover(self, stock_data):
        """计算资产周转率"""
        # 简化的资产周转率计算
        return "中等"
    
    def _assess_financial_leverage(self, stock_data):
        """评估财务杠杆"""
        debt_ratio = stock_data.get('debt_ratio', 0)
        if debt_ratio > 60:
            return "高"
        elif debt_ratio > 40:
            return "中"
        else:
            return "低"
    
    def _identify_profitability_drivers(self, stock_data):
        """识别盈利驱动因素"""
        drivers = []
        roe = stock_data.get('roe', 0)
        if roe > 15:
            drivers.append("高ROE")
        gross_margin = stock_data.get('gross_margin', 0)
        if gross_margin > 25:
            drivers.append("高毛利率")
        return drivers
    
    def _assess_scalability_potential(self, stock_data):
        """评估可扩展性潜力"""
        sector = stock_data.get('sector', '未知')
        if sector in ['科技', '互联网']:
            return "高"
        elif sector in ['消费', '医药']:
            return "中"
        else:
            return "低"
    
    def _assess_capital_intensity(self, stock_data):
        """评估资本密集度"""
        # 简化的资本密集度评估
        return "中等"
    
    def _assess_operational_leverage(self, stock_data):
        """评估运营杠杆"""
        # 简化的运营杠杆评估
        return "中等"
    
    def _assess_growth_capacity(self, stock_data):
        """评估增长容量"""
        # 简化的增长容量评估
        return "中等"
    
    def _assess_moat_strength(self, stock_data):
        """评估护城河强度"""
        roe = stock_data.get('roe', 0)
        if roe > 20:
            return "强"
        elif roe > 15:
            return "中"
        else:
            return "弱"
    
    def _assess_competitive_position(self, stock_data):
        """评估竞争地位"""
        # 简化的竞争地位评估
        return "中等"
    
    def _assess_sustainability(self, stock_data):
        """评估可持续性"""
        # 简化的可持续性评估
        return "中等"
    
    def _assess_barriers_to_entry(self, stock_data):
        """评估进入壁垒"""
        # 简化的进入壁垒评估
        return "中等"
    
    def _analyze_organic_growth(self, stock_data):
        """分析有机增长"""
        revenue_growth = stock_data.get('revenue_growth', 0)
        profit_growth = stock_data.get('profit_growth', 0)
        
        organic_growth = {
            "revenue_growth": revenue_growth,
            "profit_growth": profit_growth,
            "growth_quality": self._assess_growth_quality(revenue_growth, profit_growth),
            "sustainability": self._assess_growth_sustainability(revenue_growth)
        }
        return organic_growth
    
    def _analyze_ma_potential(self, stock_data):
        """分析并购潜力"""
        # 简化的并购潜力分析
        return {"potential": "中等"}
    
    def _analyze_market_expansion(self, stock_data, market_context):
        """分析市场扩张"""
        # 简化的市场扩张分析
        return {"potential": "中等"}
    
    def _analyze_innovation_capacity(self, stock_data):
        """分析创新能力"""
        # 简化的创新能力分析
        return {"capacity": "中等"}
    
    def _analyze_market_risks(self, stock_data, market_context):
        """分析市场风险"""
        # 简化的市场风险分析
        return {"risk_level": "中等"}
    
    def _analyze_operational_risks(self, stock_data):
        """分析运营风险"""
        # 简化的运营风险分析
        return {"risk_level": "中等"}
    
    def _analyze_financial_risks(self, stock_data):
        """分析财务风险"""
        debt_ratio = stock_data.get('debt_ratio', 0)
        financial_risks = {
            "leverage_risk": "高" if debt_ratio > 60 else "中" if debt_ratio > 40 else "低",
            "profitability_risk": self._assess_profitability_risk(stock_data),
            "liquidity_risk": self._assess_liquidity_risk(stock_data)
        }
        return financial_risks
    
    def _analyze_strategic_risks(self, stock_data):
        """分析战略风险"""
        # 简化的战略风险分析
        return {"risk_level": "中等"}
    
    def _determine_valuation_method(self, stock_data):
        """确定估值方法"""
        sector = stock_data.get('sector', '未知')
        if sector in ['金融', '银行']:
            return "PB估值"
        elif sector in ['科技', '互联网']:
            return "PE估值"
        else:
            return "综合估值"
    
    def _analyze_valuation_multiples(self, stock_data):
        """分析估值倍数"""
        pe = stock_data.get('pe', 0)
        pb = stock_data.get('pb', 0)
        
        valuation_multiples = {
            "pe": pe,
            "pb": pb,
            "relative_valuation": self._assess_relative_valuation(pe, pb)
        }
        return valuation_multiples
    
    def _determine_discount_rate(self, stock_data):
        """确定折现率"""
        # 简化的折现率确定
        return "8%"
    
    def _determine_terminal_growth(self, stock_data):
        """确定永续增长率"""
        # 简化的永续增长率确定
        return "3%"
    
    def _formulate_investment_case(self, stock_data, market_context):
        """制定投资逻辑"""
        # 简化的投资逻辑制定
        return "基于基本面分析的投资机会"
    
    def _determine_time_horizon(self, stock_data):
        """确定投资时间框架"""
        # 简化的投资时间框架确定
        return "1-3年"
    
    def _estimate_expected_return(self, stock_data):
        """预期回报率"""
        # 简化的预期回报率估计
        return "15-20%"
    
    def _assess_risk_return_profile(self, stock_data):
        """评估风险回报特征"""
        # 简化的风险回报特征评估
        return "中等风险，中等回报"
    
    def _assess_growth_quality(self, revenue_growth, profit_growth):
        """评估增长质量"""
        if profit_growth > revenue_growth:
            return "高质量"
        elif profit_growth > 0:
            return "中等质量"
        else:
            return "低质量"
    
    def _assess_growth_sustainability(self, revenue_growth):
        """评估增长可持续性"""
        if revenue_growth > 15:
            return "高"
        elif revenue_growth > 5:
            return "中"
        else:
            return "低"
    
    def _assess_profitability_risk(self, stock_data):
        """评估盈利风险"""
        net_margin = stock_data.get('net_margin', 0)
        if net_margin < 5:
            return "高"
        elif net_margin < 10:
            return "中"
        else:
            return "低"
    
    def _assess_liquidity_risk(self, stock_data):
        """评估流动性风险"""
        # 简化的流动性风险评估
        return "低"
    
    def _assess_relative_valuation(self, pe, pb):
        """评估相对估值"""
        if pe < 15 and pb < 2:
            return "低估"
        elif pe < 25 and pb < 3:
            return "合理"
        else:
            return "高估"


class EnhancedAiAnalyzer:
    """增强版AI分析器"""
    
    def __init__(self, config: dict = None):
        """初始化增强版分析器"""
        self.config = config or {}
        self.business_analyzer = BusinessLogicAnalyzer()
        
    def analyze_stock_with_deep_thinking(self, stock: dict, market_context: dict = None) -> Optional[dict]:
        """使用深度思考框架进行股票分析"""
        
        logger.info(f"[增强AI分析] 开始深度分析股票: {stock.get('code')} {stock.get('name')}")
        
        # 1. 业务逻辑深度分析
        logger.info("[增强AI分析] 执行业务逻辑深度分析...")
        business_analysis = self.business_analyzer.analyze_investment_thinking(
            stock, market_context or {}
        )
        
        # 2. 构建增强版prompt
        enhanced_prompt = self._build_enhanced_prompt(stock, business_analysis, market_context)
        
        # 3. 调用AI分析
        logger.info("[增强AI分析] 调用AI模型进行深度分析...")
        ai_result = self._call_ai_with_enhanced_prompt(enhanced_prompt)
        
        if ai_result:
            # 4. 注入深度思考分析结果
            ai_result['deep_thinking'] = {
                'business_analysis': business_analysis,
                'market_context': market_context,
                'analysis_timestamp': datetime.now().isoformat(),
                'thinking_depth_score': self._calculate_thinking_depth_score(business_analysis)
            }
            
            logger.info(f"[增强AI分析] 深度分析完成: {stock.get('code')}")
            return ai_result
        else:
            logger.warning(f"[增强AI分析] AI分析失败: {stock.get('code')}")
            return None
    
    def _build_enhanced_prompt(self, stock: dict, business_analysis: dict, market_context: dict = None) -> str:
        """构建增强版prompt"""
        
        enhanced_prompt = f"""
你是资深A股价值投资分析师，拥有超过10年的投资经验，深谙巴菲特投资哲学。

## 深度业务逻辑分析

### 商业模式解构
{self._format_business_analysis(business_analysis['business_model_understanding'])}

### 竞争优势分析
{self._format_competitive_analysis(business_analysis['competitive_advantage'])}

### 增长潜力评估
{self._format_growth_analysis(business_analysis['growth_potential'])}

### 风险因素评估
{self._format_risk_analysis(business_analysis['risk_assessment'])}

### 估值逻辑分析
{self._format_valuation_analysis(business_analysis['valuation_logic'])}

### 投资逻辑制定
{self._format_investment_thesis(business_analysis['investment_thesis'])}

## 市场环境分析
{self._format_market_context(market_context or {})}

## 公司基本面数据
- 公司名称：{stock.get('name', '')} ({stock.get('code', '')})
- 行业：{stock.get('sector', '未知')}
- PE：{stock.get('pe', 'N/A')} | PB：{stock.get('pb', 'N/A')} | ROE：{stock.get('roe', 'N/A')}%
- 市值：{stock.get('market_cap', 'N/A')}亿
- 营收增长：{stock.get('revenue_growth', 'N/A')}% | 净利增长：{stock.get('profit_growth', 'N/A')}%
- 资产负债率：{stock.get('debt_ratio', 'N/A')}% | 毛利率：{stock.get('gross_margin', 'N/A')}%

## 分析要求
请基于以上深度业务逻辑分析，输出严格的JSON格式深度分析报告：

1. **深度分析**：结合商业模式、竞争优势、增长潜力、风险因素、估值逻辑和投资逻辑，进行综合分析
2. **投资建议**：基于深度分析给出具体的投资建议
3. **风险评估**：全面评估投资风险
4. **预期回报**：分析预期回报和风险回报特征
5. **行动建议**：给出具体的投资行动建议

JSON结构必须包含：
- deep_analysis（深度分析）
- investment_advice（投资建议）
- risk_assessment（风险评估）
- expected_return（预期回报）
- action_plan（行动建议）
- quality_score（质量评分）
"""
        
        return enhanced_prompt
    
    def _format_business_analysis(self, business_model: dict) -> str:
        """格式化业务分析"""
        formatted = "### 收入模式\n"
        formatted += f"- 增长率：{business_model['revenue_model']['growth_rate']}%\n"
        formatted += f"- 模式类型：{business_model['revenue_model']['model_type']}\n"
        formatted += f"- 多元化程度：{business_model['revenue_model']['diversification']}\n"
        formatted += f"- 可持续性：{business_model['revenue_model']['sustainability']}\n\n"
        
        formatted += "### 成本结构\n"
        formatted += f"- 毛利率：{business_model['cost_structure']['gross_margin']}%\n"
        formatted += f"- 运营效率：{business_model['cost_structure']['operational_efficiency']}\n"
        formatted += f"- 成本控制：{business_model['cost_structure']['cost_control']}\n"
        formatted += f"- 规模经济：{business_model['cost_structure']['economies_of_scale']}\n\n"
        
        formatted += "### 盈利驱动因素\n"
        formatted += f"- ROE：{business_model['profitability_drivers']['roe']}%\n"
        formatted += f"- 净利率：{business_model['profitability_drivers']['net_margin']}%\n"
        formatted += f"- 关键驱动：{', '.join(business_model['profitability_drivers']['key_drivers'])}\n\n"
        
        formatted += "### 可扩展性\n"
        formatted += f"- 可扩展潜力：{business_model['scalability']['scalability_potential']}\n"
        formatted += f"- 资本密集度：{business_model['scalability']['capital_intensity']}\n"
        formatted += f"- 运营杠杆：{business_model['scalability']['operational_leverage']}\n"
        
        return formatted
    
    def _format_competitive_analysis(self, competitive_advantage: dict) -> str:
        """格式化竞争优势分析"""
        formatted = f"- 护城河强度：{competitive_advantage['moat_strength']}\n"
        formatted += f"- 竞争地位：{competitive_advantage['competitive_position']}\n"
        formatted += f"- 可持续性：{competitive_advantage['sustainability']}\n"
        formatted += f"- 进入壁垒：{competitive_advantage['barriers_to_entry']}\n"
        return formatted
    
    def _format_growth_analysis(self, growth_potential: dict) -> str:
        """格式化增长潜力分析"""
        formatted = "### 有机增长\n"
        formatted += f"- 收入增长：{growth_potential['organic_growth']['revenue_growth']}%\n"
        formatted += f"- 利润增长：{growth_potential['organic_growth']['profit_growth']}%\n"
        formatted += f"- 增长质量：{growth_potential['organic_growth']['growth_quality']}\n"
        formatted += f"- 可持续性：{growth_potential['organic_growth']['sustainability']}\n\n"
        
        formatted += "### 并购潜力\n"
        formatted += f"- 并购潜力：{growth_potential['mergers_acquisitions']['potential']}\n\n"
        
        formatted += "### 市场扩张\n"
        formatted += f"- 扩张潜力：{growth_potential['market_expansion']['potential']}\n\n"
        
        formatted += "### 创新能力\n"
        formatted += f"- 创新能力：{growth_potential['innovation_capacity']['capacity']}\n"
        
        return formatted
    
    def _format_risk_analysis(self, risk_factors: dict) -> str:
        """格式化风险分析"""
        formatted = "### 市场风险\n"
        formatted += f"- 市场风险：{risk_factors['market_risks']['risk_level']}\n\n"
        
        formatted += "### 运营风险\n"
        formatted += f"- 运营风险：{risk_factors['operational_risks']['risk_level']}\n\n"
        
        formatted += "### 财务风险\n"
        formatted += f"- 杠杆风险：{risk_factors['financial_risks']['leverage_risk']}\n"
        formatted += f"- 盈利风险：{risk_factors['financial_risks']['profitability_risk']}\n"
        formatted += f"- 流动性风险：{risk_factors['financial_risks']['liquidity_risk']}\n\n"
        
        formatted += "### 战略风险\n"
        formatted += f"- 战略风险：{risk_factors['strategic_risks']['risk_level']}\n"
        
        return formatted
    
    def _format_valuation_analysis(self, valuation_logic: dict) -> str:
        """格式化估值分析"""
        formatted = f"- 估值方法：{valuation_logic['valuation_method']}\n"
        formatted += f"- PE：{valuation_logic['valuation_multiples']['pe']}\n"
        formatted += f"- PB：{valuation_logic['valuation_multiples']['pb']}\n"
        formatted += f"- 相对估值：{valuation_logic['valuation_multiples']['relative_valuation']}\n"
        formatted += f"- 折现率：{valuation_logic['discount_rate']}\n"
        formatted += f"- 永续增长：{valuation_logic['terminal_growth']}\n"
        return formatted
    
    def _format_investment_thesis(self, investment_thesis: dict) -> str:
        """格式化投资逻辑"""
        formatted = f"- 投资逻辑：{investment_thesis['investment_case']}\n"
        formatted += f"- 投资时间：{investment_thesis['time_horizon']}\n"
        formatted += f"- 预期回报：{investment_thesis['expected_return']}\n"
        formatted += f"- 风险回报：{investment_thesis['risk_return_profile']}\n"
        return formatted
    
    def _format_market_context(self, market_context: dict) -> str:
        """格式化市场环境"""
        if not market_context:
            return "市场环境：暂无特定市场环境信息\n"
        
        formatted = "### 市场环境\n"
        for key, value in market_context.items():
            formatted += f"- {key}：{value}\n"
        return formatted
    
    def _call_ai_with_enhanced_prompt(self, prompt: str) -> Optional[dict]:
        """调用AI进行增强分析"""
        # 这里复用原有的AI调用逻辑
        # 为了演示，返回一个示例结构
        return {
            'deep_analysis': '基于深度业务逻辑分析的投资洞察',
            'investment_advice': '基于深度分析的投资建议',
            'risk_assessment': '全面的风险评估',
            'expected_return': '预期回报分析',
            'action_plan': '具体的行动建议',
            'quality_score': 0.85
        }
    
    def _calculate_thinking_depth_score(self, business_analysis: dict) -> float:
        """计算思考深度评分"""
        # 简化的深度评分计算
        depth_score = 0.0
        max_score = 0.0
        
        # 业务模型理解深度
        if 'business_model_understanding' in business_analysis:
            depth_score += 0.2
            max_score += 0.2
        
        # 竞争优势分析深度
        if 'competitive_advantage' in business_analysis:
            depth_score += 0.2
            max_score += 0.2
        
        # 增长潜力分析深度
        if 'growth_potential' in business_analysis:
            depth_score += 0.2
            max_score += 0.2
        
        # 风险评估深度
        if 'risk_assessment' in business_analysis:
            depth_score += 0.2
            max_score += 0.2
        
        # 估值逻辑深度
        if 'valuation_logic' in business_analysis:
            depth_score += 0.2
            max_score += 0.2
        
        return depth_score / max_score if max_score > 0 else 0.0


# 使用示例
def example_usage():
    """使用示例"""
    
    # 创建增强版分析器
    analyzer = EnhancedAiAnalyzer()
    
    # 示例股票数据
    stock_data = {
        'code': '000001',
        'name': '平安银行',
        'sector': '银行业',
        'pe': 6.5,
        'pb': 0.8,
        'roe': 12.5,
        'market_cap': 1500,
        'revenue_growth': 8.2,
        'profit_growth': 6.8,
        'debt_ratio': 55.3,
        'gross_margin': 35.2,
        'net_margin': 8.5
    }
    
    # 市场环境
    market_context = {
        'market_sentiment': '中性',
        'interest_rate': '稳定',
        'regulatory_environment': '中性'
    }
    
    # 执行深度分析
    result = analyzer.analyze_stock_with_deep_thinking(stock_data, market_context)
    
    if result:
        print("深度分析结果：")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("分析失败")


if __name__ == "__main__":
    example_usage()