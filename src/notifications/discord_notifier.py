"""
Discord 通知模块
用于发送 AI 分析失败等重要事件的通知
"""

import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

class DiscordNotifier:
    """Discord 通知发送器"""
    
    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url
        
    def send_ai_failure_notification(self, run_id: str, failed_count: int, 
                                   failed_reasons: list, total_count: int):
        """发送 AI 分析失败通知"""
        if not self.webhook_url:
            logger.warning("Discord webhook URL 未配置，跳过通知")
            return False
            
        try:
            # 构建消息
            title = "[告警] AI 分析失败通知"  # 零 emoji：纯文本前缀
            color = 0xff0000  # 红色
            
            # 格式化失败原因
            reasons_text = ""
            if failed_reasons:
                reasons_text = "**失败原因：**\n" + "\n".join(
                    f"• {reason}" for reason in failed_reasons[:5]  # 最多显示5个原因
                )
                if len(failed_reasons) > 5:
                    reasons_text += f"\n... 还有 {len(failed_reasons) - 5} 个失败"
            
            message = {
                "embeds": [{
                    "title": title,
                    "color": color,
                    "fields": [
                        {
                            "name": "批次ID",
                            "value": f"`{run_id}`",
                            "inline": True
                        },
                        {
                            "name": "失败数量",
                            "value": f"**{failed_count} / {total_count}**",
                            "inline": True
                        },
                        {
                            "name": "失败率",
                            "value": f"**{failed_count/total_count*100:.1f}%**",
                            "inline": True
                        }
                    ],
                    "description": reasons_text,
                    "timestamp": datetime.now().isoformat(),
                    "footer": {
                        "text": "Stock Dashboard AI 分析"
                    }
                }]
            }
            
            # 发送请求
            import requests
            response = requests.post(
                self.webhook_url,
                json=message,
                timeout=10
            )
            
            if response.status_code == 200:
                logger.info(f"Discord AI 失败通知发送成功: {failed_count}/{total_count}")
                return True
            else:
                logger.error(f"Discord 通知发送失败: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"发送 Discord 通知时发生错误: {e}")
            return False
    
    def send_daily_summary(self, date: str, screened_count: int, 
                         ai_success_count: int, ai_failed_count: int):
        """发送每日运行摘要"""
        if not self.webhook_url:
            return False
            
        try:
            title = "[摘要] 每日分析摘要"  # 零 emoji：纯文本前缀
            color = 0x00ff00 if ai_failed_count == 0 else 0xffaa00  # 绿色或橙色
            
            message = {
                "embeds": [{
                    "title": title,
                    "color": color,
                    "fields": [
                        {
                            "name": "日期",
                            "value": f"**{date}**",
                            "inline": True
                        },
                        {
                            "name": "筛选股票",
                            "value": f"**{screened_count}**",
                            "inline": True
                        },
                        {
                            "name": "AI 成功",
                            "value": f"**{ai_success_count}**",
                            "inline": True
                        },
                        {
                            "name": "AI 失败",
                            "value": f"**{ai_failed_count}**",
                            "inline": True
                        },
                        {
                            "name": "成功率",
                            "value": f"**{ai_success_count/(ai_success_count+ai_failed_count)*100:.1f}%**",
                            "inline": True
                        }
                    ],
                    "timestamp": datetime.now().isoformat(),
                    "footer": {
                        "text": "Stock Dashboard"
                    }
                }]
            }
            
            import requests
            response = requests.post(
                self.webhook_url,
                json=message,
                timeout=10
            )
            
            return response.status_code == 200
            
        except Exception as e:
            logger.error(f"发送每日摘要时发生错误: {e}")
            return False


def get_discord_notifier() -> Optional[DiscordNotifier]:
    """获取 Discord 通知器实例"""
    try:
        # 尝试从环境变量获取
        import os
        webhook_url = os.getenv('STOCK_DISCORD_WEBHOOK_URL')
        if webhook_url:
            return DiscordNotifier(webhook_url)
        
        # 或者从配置文件获取
        try:
            from configparser import ConfigParser
            config = ConfigParser()
            config.read('.env')
            if 'discord' in config and 'webhook_url' in config['discord']:
                return DiscordNotifier(config['discord']['webhook_url'])
        except:
            pass
            
        return None
    except Exception as e:
        logger.error(f"初始化 Discord 通知器失败: {e}")
        return None