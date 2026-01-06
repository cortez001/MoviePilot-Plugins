import ipaddress
from typing import List, Tuple, Dict, Any, Optional

from app.core.event import eventmanager, Event
from app.helper.downloader import DownloaderHelper
from app.helper.mediaserver import MediaServerHelper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType, WebhookEventInfo, ServiceInfo
from app.schemas.types import EventType
from app.utils.ip import IpUtils
import requests  # 用于HTTP请求


class SpeedLimiter(_PluginBase):
    # 插件基础信息
    plugin_name = "播放限速v2"
    plugin_desc = "外网播放媒体库视频时，自动对下载器进行限速。"
    plugin_icon = "Librespeed_A.png"
    plugin_version = "2.2"
    plugin_author = "Shurelol"
    author_url = "https://github.com/Shurelol"
    plugin_config_prefix = "speedlimitv2_"
    plugin_order = 11
    auth_level = 1

    # 私有属性
    _scheduler = None
    _enabled: bool = False
    _notify: bool = False
    _interval: int = 60
    _downloader: list = []
    _play_up_speed: float = 0
    _play_down_speed: float = 0
    _noplay_up_speed: float = 0
    _noplay_down_speed: float = 0
    _bandwidth: float = 0
    _allocation_ratio: str = ""
    _auto_limit: bool = False
    _limit_enabled: bool = False
    _unlimited_ips = {}
    _current_state = ""
    _exclude_path = ""

    def init_plugin(self, config: dict = None):
        if config:
            self._enabled = config.get("enabled")
            self._notify = config.get("notify")
            self._play_up_speed = float(config.get("play_up_speed")) if config.get("play_up_speed") else 0
            self._play_down_speed = float(config.get("play_down_speed")) if config.get("play_down_speed") else 0
            self._noplay_up_speed = float(config.get("noplay_up_speed")) if config.get("noplay_up_speed") else 0
            self._noplay_down_speed = float(config.get("noplay_down_speed")) if config.get("noplay_down_speed") else 0
            self._current_state = f"U:{self._noplay_up_speed},D:{self._noplay_down_speed}"
            self._exclude_path = config.get("exclude_path")

            try:
                self._bandwidth = int(float(config.get("bandwidth") or 0)) * 1000000
                self._auto_limit = True if self._bandwidth > 0 else False
            except Exception as e:
                logger.error(f"智能限速上行带宽设置错误：{str(e)}")
                self._bandwidth = 0

            self._limit_enabled = True if (self._play_up_speed or self._play_down_speed or self._auto_limit) else False
            self._allocation_ratio = config.get("allocation_ratio") or ""
            self._unlimited_ips["ipv4"] = config.get("ipv4") or ""
            self._unlimited_ips["ipv6"] = config.get("ipv6") or ""
            self._downloader = config.get("downloader") or []

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_service(self) -> List[Dict[str, Any]]:
        if self._enabled and self._limit_enabled and self._interval:
            return [
                {
                    "id": "SpeedLimiter",
                    "name": "播放限速检查服务",
                    "trigger": "interval",
                    "func": self.check_playing_sessions,
                    "kwargs": {"seconds": self._interval}
                }
            ]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{'component': 'VSwitch', 'props': {'model': 'enabled', 'label': '启用插件'}}]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{'component': 'VSwitch', 'props': {'model': 'notify', 'label': '发送通知'}}]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'multiple': True, 'chips': True, 'clearable': True,
                                            'model': 'downloader', 'label': '下载器',
                                            'items': [{"title": config.name, "value": config.name}
                                                      for config in DownloaderHelper().get_configs().values()]
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{'component': 'VTextField', 'props': {'model': 'play_up_speed', 'label': '播放限速（上传）', 'placeholder': 'KB/s'}}]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{'component': 'VTextField', 'props': {'model': 'play_down_speed', 'label': '播放限速（下载）', 'placeholder': 'KB/s'}}]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{'component': 'VTextField', 'props': {'model': 'noplay_up_speed', 'label': '未播放限速（上传）', 'placeholder': 'KB/s'}}]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{'component': 'VTextField', 'props': {'model': 'noplay_down_speed', 'label': '未播放限速（下载）', 'placeholder': 'KB/s'}}]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{'component': 'VTextField', 'props': {'model': 'bandwidth', 'label': '智能限速上行带宽', 'placeholder': 'Mbps'}}]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'allocation_ratio', 'label': '智能限速分配比例',
                                            'items': [
                                                {'title': '平均', 'value': ''},
                                                {'title': '1：9', 'value': '1:9'}, {'title': '2：8', 'value': '2:8'},
                                                {'title': '3：7', 'value': '3:7'}, {'title': '4：6', 'value': '4:6'},
                                                {'title': '6：4', 'value': '6:4'}, {'title': '7：3', 'value': '7:3'},
                                                {'title': '8：2', 'value': '8:2'}, {'title': '9：1', 'value': '9:1'},
                                            ]
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{'component': 'VTextField', 'props': {'model': 'ipv4', 'label': '不限速地址范围（ipv4）', 'placeholder': '留空默认不限速内网ipv4'}}]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{'component': 'VTextField', 'props': {'model': 'ipv6', 'label': '不限速地址范围（ipv6）', 'placeholder': '留空默认不限速内网ipv6'}}]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [{'component': 'VTextField', 'props': {'model': 'exclude_path', 'label': '不限速路径', 'placeholder': '包含该路径的媒体不限速,多个请换行'}}]
                            }
                        ]
                    }
                ]
            }
        ], {
            'enabled': False, 'notify': False, 'downloader': [],
            'play_up_speed': None, 'play_down_speed': None,
            'noplay_up_speed': None, 'noplay_down_speed': None,
            'bandwidth': None, 'allocation_ratio': '',
            'ipv4': '', 'ipv6': '', 'exclude_path': ''
        }

    def get_page(self) -> List[dict]:
        pass

    @property
    def service_infos(self) -> Optional[Dict[str, ServiceInfo]]:
        if not self._downloader:
            logger.warning("尚未配置下载器，请检查配置")
            return None
        services = DownloaderHelper().get_services(name_filters=self._downloader)
        if not services:
            logger.warning("获取下载器实例失败，请检查配置")
            return None
        active_services = {}
        for service_name, service_info in services.items():
            if service_info.instance.is_inactive():
                logger.warning(f"下载器 {service_name} 未连接，请检查配置")
            else:
                active_services[service_name] = service_info
        if not active_services:
            logger.warning("没有已连接的下载器，请检查配置")
            return None
        return active_services

    # ========== 关键修改1：新增从MediaServerHelper提取飞牛配置的方法 ==========
    def _get_feiniu_config(self) -> Optional[dict]:
        """
        从MoviePilot系统配置中提取飞牛影视的配置（host/username/password）
        """
        media_servers = MediaServerHelper().get_configs()
        for server_id, server_config in media_servers.items():
            if server_config.type == "feiniu":
                return {
                    "host": server_config.host,
                    "username": server_config.username,
                    "password": server_config.password
                }
        logger.warning("系统中未配置飞牛影视媒体服务器")
        return None

    # ========== 关键修改2：优化飞牛Token获取，直接读取系统配置 ==========
    def _feiniu_get_token(self) -> Optional[str]:
        """
        通过系统配置的飞牛用户名密码获取Token
        """
        feiniu_config = self._get_feiniu_config()
        if not feiniu_config:
            return None
        try:
            auth_url = f"{feiniu_config.get('host')}/v/api/v1/auth/login"
            payload = {
                "username": feiniu_config.get("username"),
                "password": feiniu_config.get("password")
            }
            response = requests.post(auth_url, json=payload, timeout=10)
            if response.status_code == 200:
                token = response.json().get("token")
                if token:
                    logger.info("飞牛影视Token获取成功")
                    return token
            logger.error(f"飞牛影视Token获取失败，响应码：{response.status_code}，响应内容：{response.text}")
        except Exception as e:
            logger.error(f"飞牛影视Token获取异常：{str(e)}")
        return None

    # ========== 关键修改3：优化飞牛播放状态检测，仅依赖系统配置 ==========
    def _get_feiniu_playing_sessions(self) -> List[dict]:
        """
        获取飞牛影视当前播放会话列表
        """
        token = self._feiniu_get_token()
        if not token:
            return []
        feiniu_config = self._get_feiniu_config()
        if not feiniu_config:
            return []
        
        # 优先尝试HTTP接口（部分版本可能支持）
        session_url = f"{feiniu_config.get('host')}/v/api/v1/sessions?token={token}"
        try:
            response = requests.get(session_url, timeout=10)
            if response.status_code == 200:
                sessions = response.json()
                # 过滤出正在播放且未暂停的会话
                playing_sessions = []
                for session in sessions:
                    if session.get("NowPlayingItem") and not session.get("PlayState", {}).get("IsPaused"):
                        if not self.__path_execluded(session.get("NowPlayingItem").get("Path", "")):
                            playing_sessions.append(session)
                return playing_sessions
        except Exception as e:
            logger.error(f"飞牛影视HTTP会话接口调用失败：{str(e)}")
        
        # HTTP接口失败时，可考虑降级到WebSocket（可选扩展）
        logger.warning("飞牛HTTP会话接口不可用，建议尝试WebSocket方案")
        return []

    @eventmanager.register(EventType.WebhookMessage)
    def check_playing_sessions(self, event: Event = None):
        if not self.service_infos or not self._enabled:
            return
        if event:
            event_data: WebhookEventInfo = event.event_data
            if event_data.event not in ["playback.start", "PlaybackStart", "media.play", "media.stop", "PlaybackStop", "playback.stop"]:
                return

        total_bit_rate = 0
        media_servers = MediaServerHelper().get_services()
        if not media_servers:
            return

        for server, service in media_servers.items():
            playing_sessions = []
            if service.type == "emby":
                req_url = "[HOST]emby/Sessions?api_key=[APIKEY]"
                try:
                    res = service.instance.get_data(req_url)
                    if res and res.status_code == 200:
                        sessions = res.json()
                        for session in sessions:
                            if session.get("NowPlayingItem") and not session.get("PlayState", {}).get("IsPaused"):
                                if not self.__path_execluded(session.get("NowPlayingItem").get("Path")):
                                    playing_sessions.append(session)
                except Exception as e:
                    logger.error(f"获取Emby播放会话失败：{str(e)}")
                    continue
                for session in playing_sessions:
                    if (self._unlimited_ips["ipv4"] or self._unlimited_ips["ipv6"]):
                        if not self.__allow_access(self._unlimited_ips, session.get("RemoteEndPoint")) and session.get("NowPlayingItem", {}).get("MediaType") == "Video":
                            total_bit_rate += int(session.get("NowPlayingItem", {}).get("Bitrate") or 0)
                    elif not IpUtils.is_private_ip(session.get("RemoteEndPoint")) and session.get("NowPlayingItem", {}).get("MediaType") == "Video":
                        total_bit_rate += int(session.get("NowPlayingItem", {}).get("Bitrate") or 0)

            elif service.type == "jellyfin":
                req_url = "[HOST]Sessions?api_key=[APIKEY]"
                try:
                    res = service.instance.get_data(req_url)
                    if res and res.status_code == 200:
                        sessions = res.json()
                        for session in sessions:
                            if session.get("NowPlayingItem") and not session.get("PlayState", {}).get("IsPaused"):
                                if not self.__path_execluded(session.get("NowPlayingItem").get("Path")):
                                    playing_sessions.append(session)
                except Exception as e:
                    logger.error(f"获取Jellyfin播放会话失败：{str(e)}")
                    continue
                for session in playing_sessions:
                    if (self._unlimited_ips["ipv4"] or self._unlimited_ips["ipv6"]):
                        if not self.__allow_access(self._unlimited_ips, session.get("RemoteEndPoint")) and session.get("NowPlayingItem", {}).get("MediaType") == "Video":
                            media_streams = session.get("NowPlayingItem", {}).get("MediaStreams") or []
                            for media_stream in media_streams:
                                total_bit_rate += int(media_stream.get("BitRate") or 0)
                    elif not IpUtils.is_private_ip(session.get("RemoteEndPoint")) and session.get("NowPlayingItem", {}).get("MediaType") == "Video":
                        media_streams = session.get("NowPlayingItem", {}).get("MediaStreams") or []
                        for media_stream in media_streams:
                            total_bit_rate += int(media_stream.get("BitRate") or 0)

            # ========== 关键修改4：调用独立的飞牛会话检测方法 ==========
            elif service.type == "feiniu":
                playing_sessions = self._get_feiniu_playing_sessions()
                for session in playing_sessions:
                    if (self._unlimited_ips["ipv4"] or self._unlimited_ips["ipv6"]):
                        if not self.__allow_access(self._unlimited_ips, session.get("RemoteEndPoint")) and session.get("NowPlayingItem", {}).get("MediaType") == "Video":
                            media_streams = session.get("NowPlayingItem", {}).get("MediaStreams") or []
                            for media_stream in media_streams:
                                total_bit_rate += int(media_stream.get("BitRate") or 0)
                    elif not IpUtils.is_private_ip(session.get("RemoteEndPoint")) and session.get("NowPlayingItem", {}).get("MediaType") == "Video":
                        media_streams = session.get("NowPlayingItem", {}).get("MediaStreams") or []
                        for media_stream in media_streams:
                            total_bit_rate += int(media_stream.get("BitRate") or 0)

            elif service.type == "plex":
                _plex = service.instance.get_plex()
                if _plex:
                    sessions = _plex.sessions()
                    for session in sessions:
                        bitrate = sum([m.bitrate or 0 for m in session.media])
                        playing_sessions.append({"type": session.TAG, "bitrate": bitrate, "address": session.player.address})
                    for session in playing_sessions:
                        if (self._unlimited_ips["ipv4"] or self._unlimited_ips["ipv6"]):
                            if not self.__allow_access(self._unlimited_ips, session.get("address")) and session.get("type") == "Video":
                                total_bit_rate += int(session.get("bitrate") or 0)
                        elif not IpUtils.is_private_ip(session.get("address")) and session.get("type") == "Video":
                            total_bit_rate += int(session.get("bitrate") or 0)

        if total_bit_rate:
            play_up_speed = self.__calc_limit(total_bit_rate) if self._auto_limit else self._play_up_speed
            self.__set_limiter(limit_type="播放", upload_limit=play_up_speed, download_limit=self._play_down_speed)
        else:
            self.__set_limiter(limit_type="未播放", upload_limit=self._noplay_up_speed, download_limit=self._noplay_down_speed)

    def __path_execluded(self, path: str) -> bool:
        if self._exclude_path:
            exclude_paths = self._exclude_path.split("\n")
            for exclude_path in exclude_paths:
                if exclude_path.strip() in path:
                    logger.info(f"{path} 在不限速路径：{exclude_path} 内，跳过限速")
                    return True
        return False

    def __calc_limit(self, total_bit_rate: float) -> float:
        if not self._bandwidth:
            return 10
        return round((self._bandwidth - total_bit_rate) / 8 / 1024, 2)

    def __set_limiter(self, limit_type: str, upload_limit: float, download_limit: float):
        if not self.service_infos:
            return
        state = f"U:{upload_limit},D:{download_limit}"
        if self._current_state == state:
            return
        self._current_state = state

        try:
            cnt = 0
            for download in self._downloader:
                service = self.service_infos.get(download)
                if self._auto_limit and limit_type == "播放":
                    if len(self._downloader) == 1:
                        upload_limit = int(upload_limit)
                    else:
                        if not self._allocation_ratio:
                            upload_limit = int(upload_limit / len(self._downloader))
                        else:
                            allocation_count = sum([int(i) for i in self._allocation_ratio.split(":")])
                            upload_limit = int(upload_limit * int(self._allocation_ratio.split(":")[cnt]) / allocation_count)
                            cnt += 1
                text = f"上传：{upload_limit} KB/s" if upload_limit else "上传：未限速"
                text += f"\n下载：{download_limit} KB/s" if download_limit else "\n下载：未限速"

                if service.type == 'qbittorrent':
                    service.instance.set_speed_limit(download_limit=download_limit, upload_limit=upload_limit)
                    if self._notify:
                        self.post_message(
                            mtype=NotificationType.MediaServer,
                            title="【播放限速】",
                            text=f"Qbittorrent 开始{limit_type}限速\n{text}" if (upload_limit or download_limit) else "Qbittorrent 已取消限速"
                        )
                else:
                    service.instance.set_speed_limit(download_limit=download_limit, upload_limit=upload_limit)
                    if self._notify:
                        self.post_message(
                            mtype=NotificationType.MediaServer,
                            title="【播放限速】",
                            text=f"Transmission 开始{limit_type}限速\n{text}" if (upload_limit or download_limit) else "Transmission 已取消限速"
                        )
        except Exception as e:
            logger.error(f"设置限速失败：{str(e)}")

    @staticmethod
    def __allow_access(allow_ips: dict, ip: str) -> bool:
        if not allow_ips:
            return True
        try:
            ipaddr = ipaddress.ip_address(ip)
            if ipaddr.version == 4:
                if not allow_ips.get('ipv4'):
                    return True
                for allow_ipv4 in allow_ips.get('ipv4').split(","):
                    if ipaddr in ipaddress.ip_network(allow_ipv4, strict=False):
                        return True
            elif ipaddr.ipv4_mapped:
                if not allow_ips.get('ipv4'):
                    return True
                for allow_ipv4 in allow_ips.get('ipv4').split(","):
                    if ipaddr.ipv4_mapped in ipaddress.ip_network(allow_ipv4, strict=False):
                        return True
            else:
                if not allow_ips.get('ipv6'):
                    return True
                for allow_ipv6 in allow_ips.get('ipv6').split(","):
                    if ipaddr in ipaddress.ip_network(allow_ipv6, strict=False):
                        return True
        except Exception as err:
            logger.error(f"IP合法性检查失败：{str(err)}")
            return False
        return False

    def stop_service(self):
        pass
