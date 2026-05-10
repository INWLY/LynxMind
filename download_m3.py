from modelscope import snapshot_download

print("🚀 开始从国内魔搭社区下载 BAAI/bge-m3 模型，速度会很快...")
# 将模型直接下载到当前目录下的 bge-m3 文件夹中
model_dir = snapshot_download('BAAI/bge-m3', local_dir='./bge-m3')
print(f"✅ 下载完成！模型已保存在：{model_dir}")