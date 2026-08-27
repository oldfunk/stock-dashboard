"""
增强版AI分析器 - 基于深度思考框架的股票分析

实现stock-dashboard-ai-thinking-framework框架，提供：
1. 数据穿透层：理解数字背后的商业逻辑
2. 风险穿透层：多维风险识别体系
3. 历史穿透层：历史对比和周期理解
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

# 行业特征数据库
INDUSTRY_CHARACTERISTICS = {
    "银行业": {
        "roe_normal_range": "10-15%",
        "roe_driver": "杠杆+息差",
        "risk_factors": ["信用风险", "利率风险", "监管政策"],
        "growth_expectation": "稳定增长",
        "margin_characteristic": "高息差、低净利"
    },
    "科技行业": {
        "roe_normal_range": "15-25%",
        "roe_driver": "技术壁垒+网络效应",
        "risk_factors": ["技术迭代", "竞争加剧", "人才流失"],
        "growth_expectation": "快速增长",
        "margin_characteristic": "高毛利、高研发投入"
    },
    "制造业": {
        "roe_normal_range": "8-15%",
        "roe_driver": "规模效应+效率提升",
        "risk_factors": ["原材料价格", "产能过剩", "环保政策"],
        "growth_expectation": "温和增长",
        "margin_characteristic": "中等毛利、高固定成本"
    },
    "消费行业": {
        "roe_normal_range": "12-20%",
        "roe_driver": "品牌价值+渠道控制",
        "risk_factors": ["消费趋势", "渠道变革", "竞争加剧"],
        "growth_expectation": "稳定增长",
        "margin_characteristic": "高毛利、强品牌溢价"
    }
}

# 商业模式分析模板
BUSINESS_MODEL_TEMPLATES = {
    "收入结构": {
        "analysis_focus": "收入集中度、客户粘性、增长可持续性",
        "danger_signals": [
            "单一客户占比>30%",
            "收入波动>30%",
            "新客户增长率<10%"
        ]
    },
    "成本结构": {
        "analysis_focus": "固定成本vs可变成本、规模效应",
        "danger_signals": [
            "高固定成本占比",
            "低毛利且无规模效应",
            "成本控制能力下降"
        ]
    },
    "现金流模式": {
        "analysis_focus": "经营现金流稳定性、资本支出效率",
        "danger_signals": [
            "OCF持续为负",
            "FCF波动>50%",
            "资本支出回报率低"
        ]
    }
}

# 非财务风险识别
NON_FINANCIAL_RISKS = {
    "政策风险": {
        "indicators": ["政策变化频率", "监管趋势", "政策敏感度"],
        "examples": ["教育行业'双减'政策", "房地产行业调控", "反垄断监管"]
    },
    "技术风险": {
        "indicators": ["技术迭代速度", "专利布局", "研发投入占比"],
        "examples": ["柯达错过数码时代", "诺基亚功能机衰落", "传统车企转型"]
    },
    "管理层风险": {
        "indicators": ["管理层稳定性", "股东回报记录", "战略一致性"],
        "examples": ["频繁更换CEO", "损害股东利益的决策", "战略摇摆不定"]
    }
}

class EnhancedAiAnalyzer:
    """增强版AI分析器 - 实现深度思考框架"""
    
    def __init__(self, config: dict = None):
        """初始化增强版分析器"""
        self.config = config or {}
        self.industry_db = INDUSTRY_CHARACTERISTICS
        self.business_model_templates = BUSINESS_MODEL_TEMPLATES
        self.non_financial_risks = NON_FINANCIAL_RISKS
        
    def analyze_stock_with_deep_thinking(self, stock: dict) -> Optional[dict]:
        """
        使用深度思考框架进行股票分析
        
        Args:
            stock: 股票基础数据
            
        Returns:
            增强版分析结果
        """
        # 1. 行业特性分析
        industry_analysis = self._analyze_industry_characteristics(stock)
        
        # 2. 商业模式解构
        business_model_analysis = self._analyze_business_model(stock)
        
        # 3. 风险深度分析
        risk_analysis = self._analyze_risks_deeply(stock)
        
        # 4. 历史对比分析
        historical_analysis = self._analyze_historical_context(stock)
        
        # 5. 周期性分析
        cycle_analysis = self._analyze_industry_cycle(stock)
        
        # 构建增强版prompt
        enhanced_prompt = self._build_enhanced_prompt(
            stock, industry_analysis, business_model_analysis, 
            risk_analysis, historical_analysis, cycle_analysis
        )
        
        # 调用AI分析
        result = self._call_ai_with_enhanced_prompt(enhanced_prompt)
        
        if result:
            # 注入深度思考分析结果
            result['deep_thinking'] = {
                'industry_analysis': industry_analysis,
                'business_model_analysis': business_model_analysis,
                'risk_analysis': risk_analysis,
                'historical_analysis': historical_analysis,
                'cycle_analysis': cycle_analysis
            }
            
        return result
    
    def _analyze_industry_characteristics(self, stock: dict) -> dict:
        """分析行业特性"""
        sector = stock.get('sector', '未知')
        
        if sector in self.industry_db:
            industry_info = self.industry_db[sector]
            
            # 分析当前指标是否符合行业特征
            roe = stock.get('roe', 0)
            roe_normal = self._parse_range(industry_info['roe_normal_range'])
            
            roe_assessment = ""
            if roe < roe_normal[0]:
                roe_assessment = f"ROE {roe}% 低于行业正常范围 {industry_info['roe_normal_range']}"
            elif roe > roe_normal[1]:
                roe_assessment = f"ROE {roe}% 高于行业正常范围 {industry_info['roe_normal_range']}"
            else:
                roe_assessment = f"ROE {roe}% 符合行业正常范围 {industry_info['roe_normal_range']}"
            
            return {
                'sector': sector,
                'roe_normal_range': industry_info['roe_normal_range'],
                'roe_driver': industry_info['roe_driver'],
                'risk_factors': industry_info['risk_factors'],
                'growth_expectation': industry_info['growth_expectation'],
                'margin_characteristic': industry_info['margin_characteristic'],
                'roe_assessment': roe_assessment,
                'industry_fit_score': self._calculate_industry_fit(stock, industry_info)
            }
        else:
            return {
                'sector': sector,
                'message': '未找到行业特征数据，使用通用分析',
                'industry_fit_score': 0.5  # 中性评分
            }
    
    def _analyze_business_model(self, stock: dict) -> dict:
        """解构商业模式"""
        analysis = {}
        
        # 收入结构分析
        revenue_growth = stock.get('revenue_growth', 0)
        analysis['revenue_structure'] = {
            'growth_rate': revenue_growth,
            'assessment': self._assess_revenue_growth(revenue_growth),
            'risk_level': self._assess_revenue_risk(revenue_growth)
        }
        
        # 成本结构分析
        gross_margin = stock.get('gross_margin', 0)
        analysis['cost_structure'] = {
            'gross_margin': gross_margin,
            'assessment': self._assess_gross_margin(gross_margin),
            'risk_level': self._assess_cost_risk(gross_margin)
        }
        
        # 现金流模式分析
        ocf_per_share = stock.get('ocf_per_share', 0)
        analysis['cash_flow_pattern'] = {
            'ocf_per_share': ocf_per_share,
            'assessment': self._assess_ocf(ocf_per_share),
            'risk_level': self._assess_cash_flow_risk(ocf_per_share)
        }
        
        return analysis
    
    def _analyze_risks_deeply(self, stock: dict) -> dict:
        """深度风险分析"""
        analysis = {
            'financial_risks': {},
            'non_financial_risks': {},
            'overall_risk_score': 0
        }
        
        # 财务风险深度分析
        analysis['financial_risks'] = self._analyze_financial_risks(stock)
        
        # 非财务风险分析
        analysis['non_financial_risks'] = self._analyze_non_financial_risks(stock)
        
        # 综合风险评分
        analysis['overall_risk_score'] = self._calculate_overall_risk_score(analysis)
        
        return analysis
    
    def _analyze_historical_context(self, stock: dict) -> dict:
        """历史对比分析"""
        code = stock.get('code', '')
        
        # 这里应该从数据库获取历史数据
        # 暂时使用基础数据进行分析
        historical_data = {
            'roe_trend': self._analyze_roe_trend(stock),
            'margin_trend': self._analyze_margin_trend(stock),
            'debt_trend': self._analyze_debt_trend(stock),
            'key_changes': self._identify_key_changes(stock)
        }
        
        return historical_data
    
    def _analyze_industry_cycle(self, stock: dict) -> dict:
        """行业周期分析"""
        sector = stock.get('sector', '未知')
        
        # 简化的周期判断
        cycle_assessment = {
            'cycle_stage': 'mature',  # 默认成熟期
            'characteristics': '稳定增长，竞争格局相对稳定',
            'investment_implication': '适合价值投资，关注分红和估值'
        }
        
        # 根据行业特性调整周期判断
        if sector in ['科技行业']:
            cycle_assessment = {
                'cycle_stage': 'growth',
                'characteristics': '快速增长，竞争加剧，技术迭代快',
                'investment_implication': '适合成长投资，关注技术壁垒和市场份额'
            }
        elif sector in ['制造业']:
            cycle_assessment = {
                'cycle_stage': 'mature',
                'characteristics': '温和增长，受经济周期影响较大',
                'investment_implication': '适合价值投资，关注成本控制和规模效应'
            }
        
        return cycle_assessment
    
    def _build_enhanced_prompt(self, stock: dict, industry_analysis: dict, 
                              business_model_analysis: dict, risk_analysis: dict,
                              historical_analysis: dict, cycle_analysis: dict) -> str:
        """构建增强版prompt"""
        
        enhanced_prompt = f"""
你是资深A股价值投资分析师，对{industry_analysis['sector']}有深刻理解。

## 行业背景分析
- 行业特征：{industry_analysis['margin_characteristic']}
- ROE驱动因素：{industry_analysis['roe_driver']}
- 主要风险因素：{', '.join(industry_analysis['risk_factors'])}
- 增长预期：{industry_analysis['growth_expectation']}
- 行业周期：{cycle_analysis['cycle_stage']}阶段 - {cycle_analysis['characteristics']}

## 商业模式解构
### 收入结构
- 增长率：{business_model_analysis['revenue_structure']['growth_rate']}%
- 评估：{business_model_analysis['revenue_structure']['assessment']}
- 风险等级：{business_model_analysis['revenue_structure']['risk_level']}

### 成本结构  
- 毛利率：{business_model_analysis['cost_structure']['gross_margin']}%
- 评估：{business_model_analysis['cost_structure']['assessment']}
- 风险等级：{business_model_analysis['cost_structure']['risk_level']}

### 现金流模式
- 每股OCF：{business_model_analysis['cash_flow_pattern']['ocf_per_share']}
- 评估：{business_model_analysis['cash_flow_pattern']['assessment']}
- 风险等级：{business_model_analysis['cash_flow_pattern']['risk_level']}

## 风险深度分析
### 财务风险
{self._format_financial_risks(risk_analysis['financial_risks'])}

### 非财务风险
{self._format_non_financial_risks(risk_analysis['non_financial_risks'])}

## 历史对比分析
{self._format_historical_analysis(historical_analysis)}

## 公司基本面
- 公司名称：{stock.get('name', '')} ({stock.get('code', '')})
- PE：{stock.get('pe', 'N/A')} | PB：{stock.get('pb', 'N/A')} | ROE：{stock.get('roe', 'N/A')}%
- 市值：{stock.get('market_cap', 'N/A')}亿
- 营收增长：{stock.get('revenue_growth', 'N/A')}% | 净利增长：{stock.get('profit_growth', 'N/A')}%
- 资产负债率：{stock.get('debt_ratio', 'N/A')}% | 毛利率：{stock.get('gross_margin', 'N/A')}%

## 分析要求
请结合以上深度分析，输出严格的JSON格式分析报告：

1. **深度分析**：分析数字背后的商业逻辑，结合行业特征和商业模式
2. **风险评估**：综合财务和非财务风险，给出具体风险点和应对建议
3. **历史对比**：对比历史数据变化，分析趋势可持续性
4. **投资策略**：基于深度分析给出具体的投资建议和仓位配置

JSON结构必须包含：
- analysis（深度分析）
- business_model_assessment（商业模式评估）
- risk_assessment（风险评估）
- investment_strategy（投资策略）
- deep_insights（深度洞察）
"""

        return enhanced_prompt
    
    def _call_ai_with_enhanced_prompt(self, prompt: str) -> Optional[dict]:
        """调用AI进行增强分析"""
        # 这里复用原有的AI调用逻辑
        # 为了简化，先返回一个示例结构
        return {
            'analysis': '基于深度思考框架的分析结果',
            'business_model_assessment': '商业模式评估结果',
            'risk_assessment': '风险评估结果',
            'investment_strategy': '投资策略建议',
            'deep_insights': '深度洞察'
        }
    
    # 辅助方法
    def _parse_range(self, range_str: str) -> tuple:
        """解析范围字符串，返回(min, max)"""
        if '-' in range_str:
            parts = range_str.replace('%', '').split('-')
            return float(parts[0]), float(parts[1])
        return 0, 0
    
    def _calculate_industry_fit(self, stock: dict, industry_info: dict) -> float:
        """计算行业适配度"""
        roe = stock.get('roe', 0)
        roe_range = self._parse_range(industry_info['roe_normal_range'])
        
        # ROE适配度
        if roe_range[0] <= roe <= roe_range[1]:
            roe_score = 1.0
        else:
            roe_score = max(0, 1 - abs(roe - sum(roe_range)/2) / (roe_range[1] - roe_range[0]))
        
        # 简化的适配度计算
        return roe_score * 0.7 + 0.3  # ROE占70%，其他因素占30%
    
    def _assess_revenue_growth(self, growth: float) -> str:
        """评估收入增长率"""
        if growth > 20:
            return "高增长，显示强劲的市场需求"
        elif growth > 10:
            return "稳健增长，市场地位稳定"
        elif growth > 0:
            return "温和增长，处于成熟期"
        else:
            return "负增长，面临市场挑战"
    
    def _assess_revenue_risk(self, growth: float) -> str:
        """评估收入风险"""
        if growth < 0:
            return "高风险：收入萎缩"
        elif growth < 5:
            return "中等风险：增长乏力"
        else:
            return "低风险：增长稳定"
    
    def _assess_gross_margin(self, margin: float) -> str:
        """评估毛利率"""
        if margin > 30:
            return "高毛利，强定价能力"
        elif margin > 20:
            return "中等毛利，适度定价能力"
        elif margin > 10:
            return "低毛利，价格竞争激烈"
        else:
            return "极低毛利，盈利能力堪忧"
    
    def _assess_cost_risk(self, margin: float) -> str:
        """评估成本风险"""
        if margin < 10:
            return "高风险：成本控制能力弱"
        elif margin < 20:
            return "中等风险：成本压力较大"
        else:
            return "低风险：成本控制良好"
    
    def _assess_ocf(self, ocf: float) -> str:
        """评估经营现金流"""
        if ocf > 0:
            return "正经营现金流，盈利质量良好"
        else:
            return "负经营现金流，盈利质量堪忧"
    
    def _assess_cash_flow_risk(self, ocf: float) -> str:
        """评估现金流风险"""
        if ocf > 0:
            return "低风险：现金流健康"
        else:
            return "高风险：现金流紧张"
    
    def _analyze_financial_risks(self, stock: dict) -> dict:
        """分析财务风险"""
        risks = {}
        
        # 盈利质量风险
        profit_growth = stock.get('profit_growth', 0)
        risks['profit_quality'] = {
            'risk_level': 'high' if profit_growth < 0 else 'medium' if profit_growth < 5 else 'low',
            'description': '盈利增长' + ('负增长' if profit_growth < 0 else '缓慢' if profit_growth < 5 else '良好')
        }
        
        # 负债风险
        debt_ratio = stock.get('debt_ratio', 0)
        risks['leverage'] = {
            'risk_level': 'high' if debt_ratio > 70 else 'medium' if debt_ratio > 50 else 'low',
            'description': f'负债率{debt_ratio}%，{"偏高" if debt_ratio > 50 else "合理"}'
        }
        
        # 现金流风险
        ocf_per_share = stock.get('ocf_per_share', 0)
        risks['cash_flow'] = {
            'risk_level': 'high' if ocf_per_share <= 0 else 'medium' if ocf_per_share < 1 else 'low',
            'description': f'每股OCF{ocf_per_share}，{"紧张" if ocf_per_share <= 0 else "健康"}'
        }
        
        return risks
    
    def _analyze_non_financial_risks(self, stock: dict) -> dict:
        """分析非财务风险"""
        risks = {}
        
        # 政策风险
        sector = stock.get('sector', '未知')
        risks['policy'] = {
            'risk_level': 'high' if sector in ['教育', '房地产', '金融'] else 'medium',
            'description': f'{sector}行业政策敏感度{"高" if sector in ["教育", "房地产", "金融"] else "中等"}'
        }
        
        # 技术风险
        risks['technology'] = {
            'risk_level': 'high' if sector in ['科技'] else 'low',
            'description': f'{sector}行业技术迭代{"快" if sector in ["科技"] else "慢"}'
        }
        
        # 管理层风险
        risks['management'] = {
            'risk_level': 'medium',  # 默认中等
            'description': '需要进一步分析管理层历史表现'
        }
        
        return risks
    
    def _calculate_overall_risk_score(self, risk_analysis: dict) -> float:
        """计算综合风险评分"""
        risk_scores = []
        
        for risk_type, risk_data in risk_analysis['financial_risks'].items():
            if risk_data['risk_level'] == 'high':
                risk_scores.append(3)
            elif risk_data['risk_level'] == 'medium':
                risk_scores.append(2)
            else:
                risk_scores.append(1)
        
        for risk_type, risk_data in risk_analysis['non_financial_risks'].items():
            if risk_data['risk_level'] == 'high':
                risk_scores.append(3)
            elif risk_data['risk_level'] == 'medium':
                risk_scores.append(2)
            else:
                risk_scores.append(1)
        
        if not risk_scores:
            return 1.0
        
        # 计算平均风险评分
        avg_risk = sum(risk_scores) / len(risk_scores)
        
        # 转换为0-1的风险评分
        return min(1.0, avg_risk / 3.0)
    
    def _analyze_roe_trend(self, stock: dict) -> str:
        """分析ROE趋势"""
        roe_5y = stock.get('roe_5y_avg', 0)
        roe_current = stock.get('roe', 0)
        
        if roe_5y and roe_current:
            if roe_current > roe_5y:
                return f"ROE呈上升趋势，从5年均值{roe_5y}%提升至当前{roe_current}%"
            elif roe_current < roe_5y:
                return f"ROE呈下降趋势，从5年均值{roe_5y}%下降至当前{roe_current}%"
            else:
                return f"ROE保持稳定，5年均值{roe_5y}%与当前持平"
        
        return "ROE趋势数据不足"
    
    def _analyze_margin_trend(self, stock: dict) -> str:
        """分析毛利率趋势"""
        gm_5y = stock.get('gross_margin_5y_avg', 0)
        gm_current = stock.get('gross_margin', 0)
        
        if gm_5y and gm_current:
            if gm_current > gm_5y:
                return f"毛利率呈上升趋势，从5年均值{gm_5y}%提升至当前{gm_current}%"
            elif gm_current < gm_5y:
                return f"毛利率呈下降趋势，从5年均值{gm_5y}%下降至当前{gm_current}%"
            else:
                return f"毛利率保持稳定，5年均值{gm_5y}%与当前持平"
        
        return "毛利率趋势数据不足"
    
    def _analyze_debt_trend(self, stock: dict) -> str:
        """分析负债率趋势"""
        debt_current = stock.get('debt_ratio', 0)
        
        if debt_current > 60:
            return f"负债率{debt_current}%偏高，存在财务杠杆风险"
        elif debt_current > 40:
            return f"负债率{debt_current}%适中，财务杠杆合理"
        else:
            return f"负债率{debt_current}%较低，财务保守"
    
    def _identify_key_changes(self, stock: dict) -> List[str]:
        """识别关键变化"""
        changes = []
        
        # 识别显著变化
        roe = stock.get('roe', 0)
        if roe > 20:
            changes.append("高ROE显示强劲的盈利能力")
        
        revenue_growth = stock.get('revenue_growth', 0)
        if revenue_growth < 0:
            changes.append("负增长需要关注市场需求变化")
        
        debt_ratio = stock.get('debt_ratio', 0)
        if debt_ratio > 60:
            changes.append("高负债率需要关注财务风险")
        
        return changes
    
    def _format_financial_risks(self, financial_risks: dict) -> str:
        """格式化财务风险"""
        formatted = ""
        for risk_type, risk_data in financial_risks.items():
            formatted += f"- {risk_type}: {risk_data['description']} (风险等级: {risk_data['risk_level']})\n"
        return formatted
    
    def _format_non_financial_risks(self, non_financial_risks: dict) -> str:
        """格式化非财务风险"""
        formatted = ""
        for risk_type, risk_data in non_financial_risks.items():
            formatted += f"- {risk_type}: {risk_data['description']} (风险等级: {risk_data['risk_level']})\n"
        return formatted
    
    def _format_historical_analysis(self, historical_analysis: dict) -> str:
        """格式化历史分析"""
        formatted = f"ROE趋势: {historical_analysis['roe_trend']}\n"
        formatted += f"毛利率趋势: {historical_analysis['margin_trend']}\n"
        formatted += f"负债率趋势: {historical_analysis['debt_trend']}\n"
        formatted += "关键变化:\n"
        for change in historical_analysis['key_changes']:
            formatted += f"- {change}\n"
        return formatted