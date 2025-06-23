# 飞牛NAS集成

> 此集成支持在Home Assistant中监控和控制飞牛NAS设备

## 📊 功能列表

*   ​**硬件监控**​
    *   硬盘温度
    *   硬盘健康状态
    *   硬盘通电时间
*   ​**系统监控**​
    *   系统运行状态
    *   CPU温度监控
*   ​**设备控制**​
    *   设备重启按钮
    *   设备关机按钮
    *   电源开关（支持网络唤醒关机）
*   ​**UPS信息**​
    *   UPS电量显示
    *   UPS负载
    *   UPS状态

* * *

## 🔧 飞牛NAS端配置

### SSH访问设置
1. 使用管理员账户登录SSH（非root）
2. 获取root权限：
```shell
sudo -i
```
3. 设置root密码：
```shell
passwd root
```
4. 修改SSH配置：
```shell
nano /etc/ssh/sshd_config
```
*   找到并修改：
```shell
PermitRootLogin yes      # 取消注释并修改
PasswordAuthentication yes  # 确保已启用
```
5. 保存配置：
*   Nano编辑器：`Ctrl+O` → `Enter` → `Ctrl+X`
6. 重启SSH服务：
```
systemctl restart ssh
```

### 传感器驱动配置
1. 运行传感器检测：
```shell
sensors-detect
```
*   按提示选择 `yes` 或 `y`
2. 重启NAS：
```
reboot
```

## 💻 Home Assistant安装

1.  进入**HACS商店**​
2.  添加自定义存储库：
```shell
https://github.com/anxms/fn_nas
```
3.  搜索"飞牛NAS"，点击下载
4.  ​**重启Home Assistant服务**

## ⚙️ 集成配置

1.  添加新集成 → 搜索"飞牛NAS"
2.  配置参数：
    *   NAS IP地址（必填）
    *   SSH端口（默认：22）
    *   SSH用户名和密码
    *   MAC地址（用于网络唤醒）
    *   扫描间隔（推荐≥300秒）

## ⚠️ 注意事项

*   确保NAS与Home Assistant在同一局域网
*   首次配置后请等待5分钟完成初始数据采集
*   频繁扫描可能导致NAS负载升高
*   网络唤醒功能需在BIOS中启用Wake-on-LAN

### 🔄 问题排查

# 测试SSH连接
```shell
ssh root@<NAS_IP> -p <端口>
```
若连接失败，请检查：

*   防火墙设置
*   SSH服务状态
*   路由器端口转发配置

* * *

> 📌 建议使用固定IP分配给NAS设备以确保连接稳定
