#!/usr/bin/env python3
"""
Oncoseeing HTML/CSS report generator v5.0.1

Render workflow:
1. JSON/CSV data -> Jinja2 HTML
2. HTML/CSS -> PDF via Playwright/Chromium

This implementation avoids fixed PDF text boxes. Layout is controlled by HTML/CSS
using grid, flexbox, print CSS, and page sections.
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

ASCII_RE = re.compile(r"([A-Za-z0-9%./+\-_:()]+(?:\s+[A-Za-z0-9%./+\-_:()]+)*)")

# =========================
# Cancer name mapping helpers
# =========================
CANCER_NAME_MAPPING = {
    "skin": "皮肤癌",
    "large_intestine_carcinoma": "大肠癌",
    "lung_carcinoma": "肺癌",
    "stomach_carcinoma": "胃癌",
    "haematopoietic_and_lymphoid_tissue_lymphoid_neoplasm": "淋巴组织肿瘤",
    "endometrium_carcinoma": "子宫内膜癌",
    "breast_carcinoma": "乳腺癌",
    "urinary_tract_carcinoma": "泌尿系统癌",
    "liver": "肝癌",
    "thyroid_carcinoma": "甲状腺癌",
    "upper_aerodigestive_tract_carcinoma": "上呼吸道消化道癌",
    "oesophagus_carcinoma": "食管癌",
    "kidney": "肾癌",
    "prostate_carcinoma": "前列腺癌",
    "ovary_carcinoma": "卵巢癌",
    "pancreas_carcinoma": "胰腺癌",
    "biliary_tract_carcinoma": "胆道癌",
    "cervix_carcinoma": "宫颈癌",
    "haematopoietic_and_lymphoid_tissue_haematopoietic_neoplasm": "造血系统肿瘤",
    "bone_Ewing_sarcoma": "骨尤因肉瘤",
    "bone_Ewing_sarcoma-peripheral_primitive_neuroectodermal_tumour": "骨尤因肉瘤",
}


def configured_cancer_groups() -> List[tuple[str, List[str]]]:
    """Deduplicate alternate database labels that map to the same cancer name."""
    groups: Dict[str, List[str]] = {}
    for cancer_type, cancer_name in CANCER_NAME_MAPPING.items():
        groups.setdefault(cancer_name, []).append(cancer_type)
    return [(name, types) for name, types in groups.items()]


def display_cancer_name(raw_value: Any, default: str = "未知癌种") -> str:
    text = safe_str(raw_value, "")
    if not text:
        return default
    mapped = CANCER_NAME_MAPPING.get(text)
    if mapped:
        return mapped
    return text.replace("_", " ").replace("-", " ")


# 个体化预防建议（按癌症显示名映射）
CANCER_PREVENTION_ADVICE = {
    "皮肤癌": (
        "应防范的敏感因素：长期紫外线暴晒是皮肤癌首要高危因素；反复皮肤慢性溃疡、长期慢性炎症；化学致癌物接触；既往皮肤癌病史、家族肿瘤史。\n"
        "膳食保护：多食用富含抗氧化物质新鲜蔬果，适量摄入优质蛋白。\n"
        "适宜运动：适度户外运动，严格做好防晒防护，避免正午强光暴晒，每周坚持有氧运动。\n"
        "生活规则：1、做好日常防晒，减少紫外线直接照射皮肤；2、避免接触有害化工、焦油类致癌物质；3、规律作息，保证充足睡眠。\n"
        "心理心态：轻松乐观，平常心，保持精神愉快。\n"
        "营养素补充：加强营养，忌烟酒，减少辛辣刺激性食物。\n"
        "早期症状：留意色素痣快速增大、破溃出血、边缘不规则；皮肤长期不愈合溃疡、新出现异常斑块。"
    ),
    "大肠癌": (
        "应防范的敏感因素：高发年龄45岁以上；肥胖、糖尿病；长期高脂低纤维饮食；肠息肉、溃疡性结肠炎家族史；吸烟、大量饮酒。\n"
        "膳食保护：多吃高膳食纤维蔬菜、水果、全谷物；减少红肉、加工肉制品摄入。\n"
        "适宜运动：1、有氧运动，半小时/次。2、饭后散步20分钟，帮助肠道蠕动消化。\n"
        "生活规则：1、一日3‑5餐，定时定量；2、忌烟酒及辛辣性食物；忌肥腻、不易消化食物；忌油炸、烧烤热性食物。\n"
        "心理心态：轻松乐观，平常心，保持精神愉快。\n"
        "营养素补充：可补充富含VitE、VitC、VitA等抗氧化的蔬菜水果。\n"
        "早期症状：大便习惯改变、便血、黏液便、不明原因腹痛、贫血、体重下降，既往结肠息肉史。"
    ),
    "肺癌": (
        "应防范的敏感因素：吸烟（含二手烟三手烟）；空气污染、粉尘油烟暴露；肺部慢性疾病慢阻肺、肺结核；家族肺癌肿瘤史。\n"
        "膳食保护：多吃新鲜果蔬，优质蛋白，减少油炸烟熏食物。\n"
        "适宜运动：适度户外有氧运动，呼吸新鲜空气；雾霾天气减少室外运动。\n"
        "生活规则：1、作息规律，保证充足睡眠；2、远离烟草，厨房做好油烟排风防护。\n"
        "心理心态：轻松乐观，平常心，保持精神愉快。\n"
        "营养素补充：加强营养，忌烟、酒及辛辣刺激性食物。\n"
        "早期症状：长期刺激性干咳、痰中带血丝、胸闷胸痛、反复肺部感染。"
    ),
    "胃癌": (
        "应防范的敏感因素：幽门螺杆菌Hp感染；高盐、腌制熏烤食品；霉变食物；慢性胃炎、萎缩性胃炎、胃溃疡；胃癌家族史；吸烟饮酒。\n"
        "膳食保护：饮食清淡，新鲜蔬菜水果，少腌菜、熏制品。\n"
        "适宜运动：适度中等强度有氧运动，每周坚持。\n"
        "生活规则：1、三餐定时，不要暴饮暴食；2、戒烟限酒，不吃霉变、腌制食品。\n"
        "心理心态：轻松乐观，平常心，保持精神愉快。\n"
        "营养素补充：加强营养，忌烟酒、高盐辛辣、腌制熏烤食物。\n"
        "早期症状：上腹部隐痛、腹胀嗳气、食欲减退、反酸黑便、不明原因消瘦。"
    ),
    "淋巴组织肿瘤": (
        "应防范的敏感因素：反复病毒感染；长期放射线、化学毒物接触；免疫功能低下；自身免疫疾病；家族肿瘤病史。\n"
        "膳食保护：均衡营养，新鲜果蔬、优质蛋白，提升机体免疫。\n"
        "适宜运动：中等强度有氧运动，避免过度劳累，免疫力低下时减少高强度运动。\n"
        "生活规则：1、作息规律，不要熬夜，避免过度疲劳；2、远离放射线、有害化学物质。\n"
        "心理心态：轻松乐观，平常心，保持精神愉快。\n"
        "营养素补充：加强营养，忌烟酒辛辣刺激，增强机体抵抗力。\n"
        "早期症状：无痛性淋巴结肿大，夜间盗汗，不明原因发热，体重下降，乏力瘙痒。"
    ),
    "子宫内膜癌": (
        "应防范的敏感因素：高发年龄58‑61岁；肥胖、高血压、糖尿病；长期雌激素暴露；绝经延迟；未婚少产。\n"
        "膳食保护：宜吃蓟菜、甜瓜、菱、薏米、乌梅、牡蛎、甲鱼。\n"
        "适宜运动：轻量的体育活动，每次约30分钟，消耗脂肪，每周2‑3次。\n"
        "生活规则：饮食定时定量，不能暴饮暴食。坚持低脂肪饮食，多吃瘦肉、鸡蛋、绿色蔬菜、水果等。\n"
        "心理心态：乐观，愉快，平常心处之。\n"
        "营养素补充：加强营养，避免高脂饮食，忌烟、酒及辛辣刺激性食物。忌肥腻、油煎、霉变、腌制食物。\n"
        "早期症状：积极治疗高血压、糖尿病、控制体重；警惕绝经后阴道不规则出血、异常排液。"
    ),
    "乳腺癌": (
        "应防范的敏感因素：女性月经初潮早、绝经晚；未生育晚生育；家族乳腺癌/卵巢癌家族史；肥胖；长期外源性激素；精神长期压力大。\n"
        "膳食保护：均衡饮食，蔬菜水果，减少高脂油炸食品摄入。\n"
        "适宜运动：每周坚持有氧运动，控制体重。\n"
        "生活规则：作息规律，避免长期熬夜；合理控制体重；减少不必要外源雌激素摄入。\n"
        "心理心态：保持情绪舒畅，减少焦虑压抑。\n"
        "营养素补充：加强营养，忌烟酒，减少高脂油炸刺激性食物。\n"
        "早期症状：乳房无痛肿块，乳头凹陷、血性溢液，乳房皮肤橘皮样改变，腋窝淋巴结肿大。"
    ),
    "泌尿系统癌": (
        "应防范的敏感因素：长期吸烟；化工染料、重金属职业暴露；反复泌尿系慢性感染、结石；饮水不足。\n"
        "膳食保护：大量饮水，多吃新鲜蔬果，减少腌制加工食品。\n"
        "适宜运动：适度有氧运动；避免久坐。\n"
        "生活规则：1、多饮水，不憋尿；2、戒烟限酒；3、远离化工有害毒物。\n"
        "心理心态：轻松乐观，平常心，保持精神愉快。\n"
        "营养素补充：加强营养，忌烟酒辛辣刺激食物。\n"
        "早期症状：无痛肉眼或者镜下血尿；尿频尿痛；腰腹部隐痛不适。"
    ),
    "肝癌": (
        "应防范的敏感因素：乙肝/丙肝病毒慢性感染；酗酒；黄曲霉素污染食物；脂肪肝、肝硬化；肝癌家族史。\n"
        "膳食保护：新鲜蔬菜水果，优质蛋白；杜绝霉变粮食坚果。\n"
        "适宜运动：适度运动，肝功能异常者避免剧烈运动。\n"
        "生活规则：1、戒酒；不吃霉变变质食物；2、作息，不熬夜，减轻肝脏负担。\n"
        "心理心态：轻松乐观，平常心，保持精神愉快。\n"
        "营养素补充：加强营养，忌烟酒，忌霉变、高脂油腻食物。\n"
        "早期症状：右上腹隐痛、腹胀，食欲差，乏力消瘦，黄疸。"
    ),
    "甲状腺癌": (
        "应防范的敏感因素：颈部放射线接触史；家族甲状腺肿瘤史；碘摄入异常；女性激素水平波动。\n"
        "膳食保护：合理碘摄入；新鲜果蔬，均衡膳食。\n"
        "适宜运动：适度有氧运动，避免过度劳累。\n"
        "生活规则：作息规律，情绪平稳；避免不必要颈部辐射暴露。\n"
        "心理心态：保持情绪平稳，减少长期焦虑压力。\n"
        "营养素补充：加强营养，忌烟酒及辛辣刺激性食物。\n"
        "早期症状：颈部无痛结节、颈部包块增大，声音嘶哑，吞咽不适，颈部淋巴结肿大。"
    ),
    "上呼吸道消化道癌": (
        "应防范的敏感因素：烟酒长期刺激；HPV感染；腌腊、熏烤食物；口腔咽喉慢性炎症；长期反流刺激。\n"
        "膳食保护：清淡饮食，多新鲜蔬果，少吃腌制熏烤过烫食物。\n"
        "适宜运动：适度有氧运动。\n"
        "生活规则：1、戒烟限酒；不吃过烫饮食；注意口腔卫生。\n"
        "心理心态：轻松乐观，平常心，保持精神愉快。\n"
        "营养素补充：加强营养，忌烟酒、过烫、腌制熏烤、辛辣刺激食物。\n"
        "早期症状：咽喉异物感、声音嘶哑、吞咽不畅、口腔长期溃疡不愈，痰血。"
    ),
    "食管癌": (
        "应防范的敏感因素：长期吃滚烫食物；烟酒；腌制霉变食物；胃食管反流；食管癌家族史。\n"
        "膳食保护：温热适宜饮食，多吃新鲜蔬果，减少腌制品。\n"
        "适宜运动：适度有氧运动。\n"
        "生活规则：1、不吃过烫食物；戒烟限酒；不吃霉变腌制食品。\n"
        "心理心态：轻松乐观，平常心，保持精神愉快。\n"
        "营养素补充：加强营养，忌烟酒，忌过烫、腌制、熏烤、辛辣刺激食物。\n"
        "早期症状：进食异物感、吞咽梗阻感，胸骨后不适，反酸烧心。"
    ),
    "肾癌": (
        "应防范的敏感因素：长期吸烟；肥胖、高血压；长期接触化工毒物；家族肾癌病史。\n"
        "膳食保护：新鲜蔬菜水果，优质蛋白，控制盐摄入。\n"
        "适宜运动：适度有氧运动，控制体重。\n"
        "生活规则：戒烟限酒；控制血压；远离有害化工物质。\n"
        "心理心态：轻松乐观，平常心，保持精神愉快。\n"
        "营养素补充：加强营养，忌烟酒辛辣刺激食物。\n"
        "早期症状：肉眼或镜下血尿，腰部隐痛、腹部包块。"
    ),
    "前列腺癌": (
        "应防范的敏感因素：老年男性；家族前列腺癌家族史；高脂饮食；肥胖。\n"
        "膳食保护：多吃蔬菜水果、豆制品；减少高脂红肉。\n"
        "适宜运动：适度有氧运动，控制体重，避免久坐。\n"
        "生活规则：作息规律；饮食清淡，少高脂食物。\n"
        "心理心态：轻松乐观，平常心，保持精神愉快。\n"
        "营养素补充：加强营养，忌烟酒、高脂辛辣刺激性食物。\n"
        "早期症状：尿频、夜尿增多、排尿费力，血尿，骨痛。"
    ),
    "卵巢癌": (
        "应防范的敏感因素：卵巢癌、乳腺癌家族遗传史；晚绝经；未生育；长期激素刺激。\n"
        "膳食保护：均衡膳食，多吃新鲜蔬菜水果。\n"
        "适宜运动：适度体育活动，每周坚持，控制体重。\n"
        "生活规则：作息规律；定期妇科体检。\n"
        "心理心态：乐观，愉快，平常心处之。\n"
        "营养素补充：加强营养，避免高脂饮食，忌烟、酒及辛辣刺激性食物。忌肥腻、油煎、腌制食物。\n"
        "早期症状：腹胀、腹部隐痛，消化不良，盆腔坠胀，不明原因消瘦。"
    ),
    "胰腺癌": (
        "应防范的敏感因素：长期吸烟酗酒；肥胖、糖尿病；慢性胰腺炎；胰腺癌家族史；高脂饮食。\n"
        "膳食保护：清淡易消化饮食，蔬菜水果，减少高脂高蛋白暴饮暴食。\n"
        "适宜运动：中等强度有氧运动，控制体重。\n"
        "生活规则：戒烟限酒；三餐规律，避免暴饮暴食。\n"
        "心理心态：轻松乐观，平常心，保持精神愉快。\n"
        "营养素补充：加强营养，忌烟酒、高脂油腻、暴饮暴食。\n"
        "早期症状：上腹部隐痛、腰背部放射痛；不明原因消瘦；新发糖尿病；食欲差。"
    ),
    "胆道癌": (
        "应防范的敏感因素：胆结石、慢性胆囊炎胆管炎症；胆道寄生虫；酗酒；胆道疾病家族史。\n"
        "膳食保护：低脂清淡饮食，多新鲜蔬果；三餐规律，吃早餐。\n"
        "适宜运动：适度有氧运动，控制体重。\n"
        "生活规则：规律三餐，一定要吃早餐；戒酒；减少高脂油腻。\n"
        "心理心态：轻松乐观，平常心，保持精神愉快。\n"
        "营养素补充：加强营养，忌烟酒，忌高脂油炸食物。\n"
        "早期症状：右上腹疼痛，皮肤巩膜发黄，皮肤瘙痒，消化不良。"
    ),
    "宫颈癌": (
        "应防范的敏感因素：高危型HPV持续感染；多个性伴侣；早婚早育；慢性宫颈炎症；吸烟。\n"
        "膳食保护：均衡营养，新鲜蔬菜水果，优质蛋白，提升自身免疫力。\n"
        "适宜运动：适度有氧运动，增强免疫力。\n"
        "生活规则：1、注意生殖卫生；戒烟；2、定期宫颈筛查。\n"
        "心理心态：乐观，愉快，平常心处之。\n"
        "营养素补充：加强营养，忌烟、酒及辛辣刺激性食物。\n"
        "早期症状：接触性阴道出血、不规则阴道流血，阴道异常腥臭排液。"
    ),
    "造血系统肿瘤": (
        "应防范的敏感因素：电离放射线、苯类化学毒物接触；病毒感染；免疫低下；家族血液肿瘤史。\n"
        "膳食保护：均衡膳食，新鲜果蔬优质蛋白，提升机体免疫力。\n"
        "适宜运动：适度温和有氧运动，身体虚弱避免剧烈运动。\n"
        "生活规则：规律作息，不熬夜；远离辐射、苯类化工有害物质。\n"
        "心理心态：轻松乐观，平常心，保持精神愉快。\n"
        "营养素补充：加强营养，忌烟酒辛辣刺激食物，保护免疫力。\n"
        "早期症状：持续不明原因发热，乏力面色苍白，出血瘀斑，盗汗，体重下降。"
    ),
    "骨尤因肉瘤": (
        "应防范的敏感因素：青少年及青年好发；既往骨骼损伤；遗传易感因素；放射线暴露。\n"
        "膳食保护：均衡高蛋白高维生素饮食，促进骨骼健康。\n"
        "适宜运动：适度合理运动，避免骨骼过度负重、外伤。\n"
        "生活规则：规律作息；避免不必要放射线暴露；防止骨骼外伤。\n"
        "心理心态：轻松乐观，平常心，保持精神愉快。\n"
        "营养素补充：加强营养，适当补钙、维生素，忌烟酒辛辣刺激性食物。\n"
        "早期症状：局部骨骼持续性疼痛、肿胀包块，夜间疼痛加重，局部活动受限。"
    ),
}


# =========================
# 报告补充说明（06 章节）
# =========================
# 与 disclaimer_items 一样作为静态文案维护，改文字只需改这里，无需动模板。
# 结构：paragraphs（段落）/ table（表格）/ items（编号条目）
# 引用：在文案任意位置插入 [[cite:key1,key2]]，渲染时自动替换为 [1]、[1,2] 等方括号编号。
#       编号按全文首次出现顺序自动分配，同一文献多次引用始终同号。
SUPPLEMENT_SECTIONS: List[Dict[str, Any]] = [
    {
        "title": "癌症形成机制",
        "paragraphs": [
            {
                "text": (
                    "癌症的发生通常是一个多因素、长期渐进的过程。从正常细胞发生遗传物质异常，到异常细胞持续增殖并最终形成恶性肿瘤，往往需要经历多个阶段。"
                    "长期受到遗传因素、环境因素及生活方式等影响时，细胞DNA损伤不断累积，当细胞的增殖、分化和死亡调控机制发生异常，就可能逐渐形成肿瘤。"
                    "癌症通常经历正常细胞、癌前病变、原位癌、浸润癌及转移等不同发展阶段。在早期阶段，肿瘤病灶可能较小，影像学等传统检查手段的检出能力有限，"
                    "而肿瘤相关DNA异常有可能先于明显的影像学改变出现，因此ctDNA检测可作为癌症早期风险评估的辅助手段。"
                ),
            },
        ],
    },
    {
        "title": "循环肿瘤DNA (ctDNA)",
        "paragraphs": [
            {
                "text": (
                    "循环肿瘤DNA（circulating tumor DNA, ctDNA）是一类存在于血浆、血清、脑脊液等体液中的细胞外游离DNA，"
                    "主要来源于坏死、凋亡的肿瘤细胞，以及肿瘤细胞分泌的外泌体和循环肿瘤细胞，片段长度多集中在160–180 bp。"
                    "ctDNA属于游离DNA（cell-free DNA, cfDNA）的特殊亚型，在外周血中占比极低，仅为0.1%–1%，因此检测难度较高。"
                ),
            },
            {
                "text": (
                    "已有多项研究表明，肿瘤患者血液中的基因突变片段数量是健康人群的5–10倍，且突变负荷与肿瘤疾病进展呈显著正相关。"
                    "肿瘤负荷越高，血液中ctDNA突变片段丰度越高，对应的基因突变检测阳性率也随之提升[[cite:catarino2012,ulivi2013,elshimali2013]]。"
                ),
            },
            {
                "text": (
                    "ctDNA无创个体化诊疗基因检测技术，可通过采集患者外周血分离ctDNA并完成高通量测序，精准分析肿瘤药物相关的基因变异特征，"
                    "系统解读基因变异与肿瘤靶向药物、化疗药物的对应关系，能够为临床医师制定肿瘤个体化用药方案、开展动态疗效评估提供可靠的分子依据。"
                ),
            },
        ],
    },
    {
        "title": "检测技术说明",
        "paragraphs": [
            {
                "text": (
                    "本检测采用外周血液体活检方式开展ctDNA分子检测，依托新一代高通量测序（NGS）技术，联合UMI分子标签纠错技术，突破传统检测技术短板[[cite:nbt3520,nbt2514]]。"
                    "该技术体系可稳定捕获低至0.1%的低频基因突变，有效过滤测序背景干扰、显著降低检测假阳性率，全面提升低频肿瘤变异检测的灵敏度、准确度与结果可信度。"
                ),
            },
            {
                "text": (
                    "本检测具备无创微创、取样简便、可反复动态检测的技术优势，无需穿刺、手术等有创取材，即可系统性获取全身肿瘤分子信息，"
                    "有效规避传统组织活检取样局限及肿瘤异质性导致的检测偏倚[[cite:nrc2017]]。检测通过提取外周血游离DNA完成高通量测序分析，"
                    "结合基因变异类型、突变丰度、权威肿瘤数据库及循证医学证据开展综合研判，可辅助评估受检者肿瘤潜在发病风险，"
                    "为临床个体化筛查与精准诊疗提供可靠的分子学参考依据[[cite:zhang2021]]。"
                ),
            },
            {
                "text": (
                    "本检测存在固有技术局限，仅适用于肿瘤辅助筛查与风险评估，不可单独作为恶性肿瘤确诊或排除的诊断依据。"
                    "肿瘤早期病灶负荷极低，ctDNA释放量有限，可能因低于检测阈值出现假阴性结果。因此，阴性检测结果无法完全排除早期肿瘤病变，"
                    "阳性及异常检测结果亦不能直接判定为恶性肿瘤。所有检测数据均需结合受检者临床病史、影像学、内镜、病理等多维度临床资料，"
                    "由专业医师进行综合性临床研判与确诊。"
                ),
            },
        ],
    },
    {
        "title": "检测方法",
        "table": {
            "headers": ["步骤", "说明"],
            "rows": [
                ["采样 + 分离", "抽血浆，提取游离 cfDNA"],
                ["单分子标签", "双链 UMI 接头连接，给每一条 cfDNA 分子打上专属标签"],
                ["建库", "PCR 扩增（扩增会引入错误，后续靠 UMI 过滤）"],
                ["测序", "超高深度测序 > 10000×"],
                ["分析", "根据 UMI 分组，去掉 PCR / 测序假阳性，识别 0.05% 低频突变"],
            ],
        },
    },
    {
        "title": "临床意义",
        "items": [
            {
                "title": "个体化治疗参考",
                "text": "检测肿瘤相关基因变异可识别潜在治疗靶点，结合临床研究、药物说明书及诊疗指南，分析变异与靶向、免疫等治疗策略的关系，为个体化治疗方案提供分子参考。",
            },
            {
                "title": "耐药相关变异分析",
                "text": "治疗过程中肿瘤克隆演化可产生耐药相关基因改变；动态检测 ctDNA 可观察分子特征变化、发现潜在耐药变异，为评估治疗反应和调整后续策略提供参考[[cite:nature12065]]。",
            },
            {
                "title": "疾病动态监测",
                "text": "ctDNA 可重复采样，支持治疗前后多时间点检测以比较分子变化趋势；其水平或特定变异的变化可作为动态监测的辅助指标，用于评估治疗反应、分子残留及复发风险[[cite:dawson2013]]。",
            },
            {
                "title": "肿瘤分子异质性辅助评估",
                "text": "肿瘤存在空间与时间异质性，单部位组织活检反映的信息有限；ctDNA 源自多个肿瘤细胞群，可补充组织检测的不足，从血液层面更全面地反映肿瘤分子改变[[cite:nrc2017]]。",
            },
        ],
    },
    {
        "title": "适用人群",
        "items": [
            {"title": "健康体检及癌症早筛人群", "text": "希望通过血液检测了解自身潜在肿瘤相关风险的人群。"},
            {"title": "中高龄人群", "text": "年龄增长使多种恶性肿瘤风险升高[[cite:alexandrov2013]]，可结合常规体检进行综合筛查。"},
            {"title": "具有癌症家族史的人群", "text": "存在肿瘤家族聚集风险，希望进一步评估肿瘤风险的人群。"},
            {"title": "具有吸烟、饮酒等危险因素的人群", "text": "可结合个人暴露史及常规筛查进行综合风险评估。"},
            {"title": "既往存在异常体检结果的人群", "text": "如影像学发现可疑结节、息肉等，可将 ctDNA 检测作为进一步风险评估的辅助参考。"},
            {"title": "需定期肿瘤风险管理的人群", "text": "可在专业医生指导下结合其他筛查手段定期评估。"},
        ],
    },
    {
        "title": "特别提示",
        "paragraphs": [
            {
                "text": (
                    "本检测主要用于癌症早期风险评估和辅助筛查，不作为确诊依据。风险升高不代表已患癌，未检出异常也不能完全排除早期肿瘤；"
                    "结果异常时应结合年龄、家族史、临床症状及影像学、内镜、病理等检查，由专业医生综合判断。"
                ),
            },
        ],
    },
]


# =========================
# 参考文献（Vancouver 数字编号制）
# =========================
# 每条文献用稳定的 key 标识。正文通过 key 引用（{{ citation('key') }}），
# 编号按“首次在正文中出现的顺序”自动生成，新增文献无需改动模板。
REFERENCES: List[Dict[str, Any]] = [
    {
        "key": "fiala2018",
        "authors": "Fiala C, Diamandis EP.",
        "title": "Utility of circulating tumor DNA in cancer diagnostics with emphasis on early detection.",
        "journal": "BMC Med.",
        "date": "2018 Dec",
        "volume": "16",
        "issue": "1",
        "pages": "166",
        "doi": "10.1186/s12916-018-1157-9",
    },
    {
        "key": "catarino2012",
        "authors": "Catarino R, Coelho A, Araújo A, Gomes M, Nogueira A, Lopes C, et al.",
        "title": "Circulating DNA: Diagnostic Tool and Predictive Marker for Overall Survival of NSCLC Patients.",
        "journal": "PLoS ONE.",
        "date": "2012 Jun 12",
        "volume": "7",
        "issue": "6",
        "pages": "e38559",
        "doi": "10.1371/journal.pone.0038559",
    },
    {
        "key": "ulivi2013",
        "authors": "Ulivi P, Mercatali L, Casoni GL, Scarpi E, Bucchi L, Silvestrini R, et al.",
        "title": "Multiple Marker Detection in Peripheral Blood for NSCLC Diagnosis.",
        "journal": "PLoS ONE.",
        "date": "2013 Feb 26",
        "volume": "8",
        "issue": "2",
        "pages": "e57401",
        "doi": "10.1371/journal.pone.0057401",
    },
    {
        "key": "elshimali2013",
        "authors": "Elshimali Y, Khaddour H, Sarkissyan M, Wu Y, Vadgama J.",
        "title": "The Clinical Utilization of Circulating Cell Free DNA (CCFDNA) in Blood of Cancer Patients.",
        "journal": "IJMS.",
        "date": "2013 Sep 13",
        "volume": "14",
        "issue": "9",
        "pages": "18925–58",
        "doi": "10.3390/ijms140918925",
    },
    {
        "key": "nbt3520",
        "authors": "Newman AM, Lovejoy AF, Klass DM, Kurtz DM, Chabon JJ, Scherer F, et al.",
        "title": "Integrated digital error suppression for improved detection of circulating tumor DNA.",
        "journal": "Nat Biotechnol.",
        "date": "2016 May",
        "volume": "34",
        "issue": "5",
        "pages": "547–55",
        "doi": "10.1038/nbt.3520",
    },
    {
        "key": "nbt2514",
        "authors": "Cibulskis K, Lawrence MS, Carter SL, Sivachenko A, Jaffe D, Sougnez C, et al.",
        "title": "Sensitive detection of somatic point mutations in impure and heterogeneous cancer samples.",
        "journal": "Nat Biotechnol.",
        "date": "2013 Mar",
        "volume": "31",
        "issue": "3",
        "pages": "213–9",
        "doi": "10.1038/nbt.2514",
    },
    {
        "key": "nrc2017",
        "authors": "Wan JCM, Massie C, Garcia-Corbacho J, Mouliere F, Brenton JD, Caldas C, et al.",
        "title": "Liquid biopsies come of age: towards implementation of circulating tumour DNA.",
        "journal": "Nat Rev Cancer.",
        "date": "2017 Apr",
        "volume": "17",
        "issue": "4",
        "pages": "223–38",
        "doi": "10.1038/nrc.2017.7",
    },
    {
        "key": "zhang2021",
        "authors": "Zhang Y, Yao Y, Xu Y, Li L, Gong Y, Zhang K, et al.",
        "title": "Pan-cancer circulating tumor DNA detection in over 10,000 Chinese patients.",
        "journal": "Nat Commun.",
        "date": "2021 Jan 4",
        "volume": "12",
        "issue": "1",
        "pages": "11",
        "doi": "10.1038/s41467-020-20162-8",
    },
    {
        "key": "nature12065",
        "authors": "Murtaza M, Dawson SJ, Tsui DWY, Gale D, Forshew T, Piskorz AM, et al.",
        "title": "Non-invasive analysis of acquired resistance to cancer therapy by sequencing of plasma DNA.",
        "journal": "Nature.",
        "date": "2013 May",
        "volume": "497",
        "issue": "7447",
        "pages": "108–12",
        "doi": "10.1038/nature12065",
    },
    {
        "key": "dawson2013",
        "authors": "Dawson SJ, Tsui DWY, Murtaza M, Biggs H, Rueda OM, Chin SF, et al.",
        "title": "Analysis of Circulating Tumor DNA to Monitor Metastatic Breast Cancer.",
        "journal": "New England Journal of Medicine.",
        "date": "2013 Mar 28",
        "volume": "368",
        "issue": "13",
        "pages": "1199–209",
        "doi": "10.1056/NEJMoa1213261",
    },
    {
        "key": "alexandrov2013",
        "authors": "Alexandrov LB, Nik-Zainal S, Wedge DC, Campbell PJ, Stratton MR.",
        "title": "Deciphering Signatures of Mutational Processes Operative in Human Cancer.",
        "journal": "Cell Reports.",
        "date": "2013 Jan",
        "volume": "3",
        "issue": "1",
        "pages": "246–59",
        "doi": "10.1016/j.celrep.2012.12.008",
    },
]


def format_citation_marker(numbers: List[int]) -> str:
    """把文献编号折叠成 Vancouver 方括号形式：连续用 `-`，非连续用 `,`。

    示例：[1,2,3] -> [1-3]，[1,3,5] -> [1,3,5]
    """
    unique = sorted(set(numbers))
    if not unique:
        return ""
    groups = []
    start = prev = unique[0]
    for number in unique[1:]:
        if number == prev + 1:
            prev = number
            continue
        groups.append((start, prev))
        start = prev = number
    groups.append((start, prev))
    return "[" + ",".join(str(begin) if begin == end else f"{begin}-{end}" for begin, end in groups) + "]"


def format_reference(ref: Dict[str, Any]) -> str:
    """按 Vancouver 格式渲染单条参考文献（编号不换行，见 \\u00a0）。

    支持两类文献：
      * 网络文献（含 url）：标题. 期刊 [Internet]. [cited 日期]. Available from: URL.
      * 期刊文献：作者 标题. 期刊 日期;卷(期):页码. doi:xxx.
    """
    url = safe_str(ref.get("url"), "")
    if url:
        text = f"[{safe_str(ref.get('id'), '')}]\u00a0"
        title = safe_str(ref.get("title"), "")
        journal = safe_str(ref.get("journal"), "")
        cited = safe_str(ref.get("cited"), "")
        if title:
            text += title
        if journal:
            text += f" {journal}"
        text += " [Internet]."
        if cited:
            text += f" [cited {cited}]."
        return text + f" Available from: {url}."

    text = f"[{safe_str(ref.get('id'), '')}]\u00a0{safe_str(ref.get('authors'), '')}"
    for field in ("title", "journal"):
        value = safe_str(ref.get(field), "")
        if value:
            text += f" {value}"
    date = safe_str(ref.get("date"), "")
    volume = safe_str(ref.get("volume"), "")
    issue = safe_str(ref.get("issue"), "")
    pages = safe_str(ref.get("pages"), "")
    if volume or pages:
        imprint = f"{volume}({issue}):{pages}." if volume else f"{pages}."
        imprint = f"{date};{imprint}" if date else imprint
    else:
        imprint = f"{date}." if date else ""
    if imprint:
        text += f" {imprint}"
    doi = safe_str(ref.get("doi"), "")
    if doi:
        text += f" doi:{doi}."
    return text


class CitationRegistry:
    """按正文首次出现顺序为参考文献自动编号。

    模板渲染是顺序执行的，参考文献章节位于文末，因此渲染到该章节时
    所有正文引用都已登记完毕，可直接按首次出现顺序输出。
    """

    def __init__(self, references: Optional[List[Dict[str, Any]]] = None):
        source = REFERENCES if references is None else references
        self._references = {
            safe_str(ref.get("key"), ""): ref
            for ref in source
            if safe_str(ref.get("key"), "")
        }
        self._order: List[str] = []

    def reset(self) -> None:
        self._order = []

    def cite(self, keys: Any) -> str:
        """登记一次引用并返回方括号编号，如 [1]、[1-3]、[1,3,5]。"""
        if isinstance(keys, str):
            key_list = [part.strip() for part in keys.split(",")]
        elif isinstance(keys, (list, tuple, set)):
            key_list = [safe_str(key, "") for key in keys]
        else:
            key_list = [safe_str(keys, "")]
        numbers = []
        for key in key_list:
            if not key or key not in self._references:
                continue
            if key not in self._order:
                self._order.append(key)
            numbers.append(self._order.index(key) + 1)
        return format_citation_marker(numbers)

    def used_references(self) -> List[Dict[str, Any]]:
        """只返回正文实际引用过的文献，编号即首次出现顺序。"""
        used = []
        for index, key in enumerate(self._order, start=1):
            ref = dict(self._references[key])
            ref["id"] = index
            used.append(ref)
        return used


def safe_str(value: Any, default: str = "-") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def as_probability(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, str):
        text = value.strip().replace("％", "%")
        if not text:
            return 0.0
        if text.endswith("%"):
            return clamp(float(text[:-1]) / 100.0)
        value = float(text)
    value = float(value)
    if value > 1.0:
        value = value / 100.0
    return clamp(value)


def pct(value: Any, digits: int = 1) -> str:
    return f"{as_probability(value) * 100:.{digits}f}%"


def format_cds_hgvs(value: Any) -> str:
    """将 CDS 表述统一为 HGVS 规范形式。

    HGVS 规定单碱基替换写作 ``c.<位置><参考碱基>><突变碱基>``（如 ``c.818G>A``）。
    旧数据的 CDS 常写作 ``c.<参考碱基><位置><突变碱基>``（如 ``c.C580T``），本函数
    将这种旧格式改写为 HGVS 规范形式；已是规范形式的 del/ins/dup 等保持不变。

    示例：
        c.C580T  -> c.580C>T
        c.G37T   -> c.37G>T
        c.818G>A -> c.818G>A（保持不变）
        c.762_793del -> c.762_793del（保持不变）
    """
    text = safe_str(value, "")
    if not text:
        return ""
    legacy = re.match(r"^c\.([ACGTNacgtn])(\d+)([ACGTNacgtn])$", text)
    if legacy:
        ref, pos, alt = legacy.group(1), legacy.group(2), legacy.group(3)
        return f"c.{pos}{ref.upper()}>{alt.upper()}"
    return text


def mutation_display(cds: Any, hgvsg: Any = "", default: str = "-") -> str:
    """报告的突变展示统一使用 CDS（HGVS 规范形式）；CDS 缺失时回退到 HGVSG。"""
    cds_text = format_cds_hgvs(cds)
    if cds_text:
        return cds_text
    return safe_str(hgvsg, default)


# 突变比例 -> 相当于体内含有携带该突变的变异细胞个数
# 表格自上而下为阈值边界，相邻两行构成一个 AF 区间。
AF_CELL_TABLE: List[tuple[str, float, int]] = [
    ("1%", 0.01, 9),
    ("0.1%", 0.001, 8),
    ("0.01%", 0.0001, 7),
]


def af_cell_range(value: Any) -> Markup:
    """根据突变比例(AF)查询体内携带该突变的变异细胞数量范围。

    示例：AF=0.5% 落在 0.1%~1% 之间，返回 10^8~10^9。
    """
    af = as_probability(value)
    if af >= AF_CELL_TABLE[0][1]:
        return Markup(f"~10<sup>{AF_CELL_TABLE[0][2]}</sup>")
    for i in range(len(AF_CELL_TABLE) - 1):
        upper_label, upper_threshold, upper_exp = AF_CELL_TABLE[i]
        lower_label, lower_threshold, lower_exp = AF_CELL_TABLE[i + 1]
        if af >= lower_threshold:
            return Markup(f"10<sup>{lower_exp}</sup>~10<sup>{upper_exp}</sup>")
    return Markup(f"&lt;10<sup>{AF_CELL_TABLE[-1][2]}</sup>")


def cancer_risk_label(value: Any) -> str:
    """Normalize cancer-specific risk to the report's three display levels.

    Legacy numeric probability values are accepted only while loading old input
    files; the normalized report data never retains a numeric cancer
    probability or uses one for ordering.
    """
    text = safe_str(value, "").replace("风险", "")
    if text in {"高", "中", "低"}:
        return text
    probability = as_probability(value)
    return "高" if probability >= 0.60 else "中" if probability >= 0.30 else "低"


def fmt_int(value: Any) -> str:
    try:
        return f"{int(float(value)):,}"
    except Exception:
        return safe_str(value)


def escape_and_wrap(text: str) -> str:
    """转义后把拉丁文/数字片段包进 Times 字体的 span。

    注意：仅转义 ``<`` 与 ``&``，保留字面 ``>``。``>`` 在 HTML 中是普通文本
    字符，无需转义；若转义成 ``&gt;``，部分 PDF 渲染引擎（如 WeasyPrint）
    会原样输出 ``&gt;`` 而非 ``>``，导致突变位点（如 ``c.580C>T``）显示异常。
    """
    if not text:
        return ""
    escaped = text.replace("&", "&amp;").replace("<", "&lt;")
    def repl(match: re.Match[str]) -> str:
        return f'<span class="latin">{match.group(1)}</span>'
    return ASCII_RE.sub(repl, escaped)


def mixed_span(value: Any) -> Markup:
    """Wrap Latin/number runs in a dedicated Times-family span.

    Chinese remains in the page default CJK font. This avoids CJK font rendering
    Latin letters inside mixed strings.
    """
    return Markup(escape_and_wrap(safe_str(value, "")))


# 段中引用占位符：文案里写 [[cite:key1,key2]]，渲染时替换为 [1]、[1,2] 等方括号编号。
CITE_PLACEHOLDER_RE = re.compile(r"\[\[cite:([A-Za-z0-9_,\-]+)\]\]")


def make_rich_text(registry: "CitationRegistry"):
    """生成 rich_text 过滤器。

    与 mixed 的区别：额外把文案中的 [[cite:...]] 占位符替换成引用标记。
    编号由 registry 按渲染顺序自动分配，因此必须在模板渲染时调用。
    """

    def rich_text(value: Any) -> Markup:
        text = safe_str(value, "")
        if not text:
            return Markup("")
        parts = []
        pos = 0
        for match in CITE_PLACEHOLDER_RE.finditer(text):
            if match.start() > pos:
                parts.append(escape_and_wrap(text[pos:match.start()]))
            keys = [k.strip() for k in match.group(1).split(",") if k.strip()]
            marker = registry.cite(keys)
            if marker:
                parts.append(f'<span class="citation">{marker}</span>')
            pos = match.end()
        if pos < len(text):
            parts.append(escape_and_wrap(text[pos:]))
        return Markup("".join(parts))

    return rich_text


def parse_int(value: Any) -> int:
    try:
        return int(float(value))
    except Exception:
        return 0


def infer_cancer_type_from_query(query: Optional[str], fallback: Optional[str] = None, cancer_col: str = "cancer_type") -> Optional[str]:
    if fallback:
        return safe_str(fallback)
    if not query:
        return None

    pattern = re.compile(rf"\b{re.escape(cancer_col)}\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)
    match = pattern.search(query)
    if match:
        return safe_str(match.group(1))
    return None


def infer_cancer_type_from_sqlite(sqlite_db: Optional[Path], query: Optional[str] = None, cancer_col: str = "cancer_type") -> Optional[str]:
    if not sqlite_db or not sqlite_db.exists():
        return None

    try:
        with sqlite3.connect(str(sqlite_db)) as conn:
            conn.row_factory = sqlite3.Row
            if query:
                rows = conn.execute(query).fetchall()
            else:
                rows = conn.execute(f"SELECT DISTINCT {cancer_col} FROM mutation WHERE {cancer_col} IS NOT NULL ORDER BY {cancer_col}").fetchall()
            if not rows:
                return None
            for row in rows:
                value = safe_str(row[cancer_col])
                if value:
                    return value
    except Exception:
        return None
    return None


def build_cancer_match_summary(
    sqlite_db: Optional[Path],
    hgvsgs: List[str],
    cancer_col: str = "cancer_type",
    hgvsg_col: str = "hgvsg",
    mutation_table: str = "mutation",
) -> List[Dict[str, Any]]:
    if not sqlite_db or not sqlite_db.exists():
        return []

    normalized_hgvsgs = [safe_str(value, "") for value in hgvsgs if safe_str(value, "")]
    if not normalized_hgvsgs:
        return []

    try:
        with sqlite3.connect(str(sqlite_db)) as conn:
            conn.row_factory = sqlite3.Row
            placeholders = ",".join("?" for _ in normalized_hgvsgs)
            sql = f"SELECT DISTINCT {cancer_col}, {hgvsg_col} FROM {mutation_table} WHERE {hgvsg_col} IN ({placeholders})"
            rows = conn.execute(sql, normalized_hgvsgs).fetchall()
    except Exception:
        return []

    matched_by_cancer: defaultdict[str, set[str]] = defaultdict(set)
    for row in rows:
        cancer_value = safe_str(row[cancer_col])
        hgvsg_value = safe_str(row[hgvsg_col])
        if cancer_value and hgvsg_value:
            matched_by_cancer[cancer_value].add(hgvsg_value)

    summary = [
        {
            "cancer_type": cancer_value,
            "cancer_name": display_cancer_name(cancer_value),
            "site_count": len(matched_hgvsgs),
        }
        for cancer_value, matched_hgvsgs in matched_by_cancer.items()
    ]
    summary.sort(key=lambda item: (-item["site_count"], item["cancer_name"]))
    return summary


def build_cancer_findings(
    sqlite_db: Optional[Path],
    variant_rows: List[Dict[str, Any]],
    cancer_col: str = "cancer_type",
    hgvsg_col: str = "hgvsg",
    sample_col: str = "cosmic_sample_id",
    gene_col: str = "gene_symbol",
    mutation_table: str = "mutation",
) -> List[Dict[str, Any]]:
    """Build per-cancer details, distributions and gene statistics."""
    if not sqlite_db or not sqlite_db.exists() or not variant_rows:
        return []

    variants_by_hgvsg = {
        safe_str(row.get("hgvsg"), ""): row
        for row in variant_rows
        if safe_str(row.get("hgvsg"), "")
    }
    if not variants_by_hgvsg:
        return []

    try:
        with sqlite3.connect(str(sqlite_db)) as conn:
            conn.row_factory = sqlite3.Row
            placeholders = ",".join("?" for _ in variants_by_hgvsg)
            rows = conn.execute(
                f"SELECT DISTINCT {cancer_col}, {hgvsg_col}, {gene_col} FROM {mutation_table} "
                f"WHERE {hgvsg_col} IN ({placeholders})",
                list(variants_by_hgvsg),
            ).fetchall()
    except Exception:
        return []

    grouped: defaultdict[str, List[Dict[str, Any]]] = defaultdict(list)
    seen: defaultdict[str, set[str]] = defaultdict(set)
    genes_by_cancer: defaultdict[str, set[str]] = defaultdict(set)
    for row in rows:
        cancer_type = safe_str(row[cancer_col], "")
        hgvsg = safe_str(row[hgvsg_col], "")
        if cancer_type and hgvsg in variants_by_hgvsg and hgvsg not in seen[cancer_type]:
            grouped[cancer_type].append(variants_by_hgvsg[hgvsg])
            seen[cancer_type].add(hgvsg)
        gene = safe_str(row[gene_col], "")
        if cancer_type and gene:
            genes_by_cancer[cancer_type].add(gene)

    findings = []
    try:
        with sqlite3.connect(str(sqlite_db)) as conn:
            conn.row_factory = sqlite3.Row
            for cancer_type, variants in grouped.items():
                sample_rows = conn.execute(
                    f"SELECT {sample_col}, COUNT(DISTINCT {hgvsg_col}) AS site_count FROM {mutation_table} "
                    f"WHERE {cancer_col} = ? GROUP BY {sample_col}", (cancer_type,)
                ).fetchall()
                all_counts = [int(row["site_count"]) for row in sample_rows if row[sample_col] is not None]
                truncated_counts = [count for count in all_counts if count <= 30]
                distribution_counts = Counter(truncated_counts)
                distribution_base = len(truncated_counts)
                distribution = [{"site_count": count, "percentage": distribution_counts[count] / distribution_base}
                                for count in sorted(distribution_counts)] if distribution_base else []
                ordered = sorted(truncated_counts)
                middle = len(ordered) // 2
                median = (float(ordered[middle]) if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2) if ordered else 0.0
                total_cases = len(all_counts)
                gene_statistics = []
                for gene in sorted(genes_by_cancer[cancer_type]):
                    gene_case_count = conn.execute(
                        f"SELECT COUNT(DISTINCT {sample_col}) FROM {mutation_table} WHERE {cancer_col} = ? AND {gene_col} = ?",
                        (cancer_type, gene),
                    ).fetchone()[0]
                    gene_statistics.append({"gene": gene, "total_cases": total_cases, "gene_case_count": gene_case_count,
                                            "ratio": gene_case_count / total_cases if total_cases else 0.0})
                variants.sort(key=lambda item: item.get("af", 0.0), reverse=True)
                customer_count = len(variants)
                risk_level = "低风险" if customer_count < median else "中风险" if customer_count == median else "高风险"
                # 为每个 variant 计算该癌症种类中含有此 hgvsg 的样本数
                for v in variants:
                    hgvsg = v.get("hgvsg", "")
                    hgvsg_sample_count = 0
                    if hgvsg:
                        hgvsg_sample_count = conn.execute(
                            f"SELECT COUNT(DISTINCT {sample_col}) FROM {mutation_table} "
                            f"WHERE {cancer_col} = ? AND {hgvsg_col} = ?",
                            (cancer_type, hgvsg),
                        ).fetchone()[0]
                    v["hgvsg_sample_count"] = hgvsg_sample_count
                    # 该癌症种类中检测到此 variant 所属基因的样本数（优先取本 variant 自身基因，否则回退到 gene_statistics）
                    gene_name = v.get("gene", "")
                    v["gene_case_count"] = next(
                        (g["gene_case_count"] for g in gene_statistics if g["gene"] == gene_name), 0
                    )
                findings.append({
                    "cancer_type": cancer_type, "cancer_name": display_cancer_name(cancer_type), "site_count": customer_count,
                    "customer_site_count": customer_count, "median_site_count": median, "risk_level": risk_level,
                    "distribution": distribution, "database_case_count": total_cases, "gene_statistics": gene_statistics,
                    "variants": [{"hgvsg": row.get("hgvsg", "-"), "gene": row.get("gene", ""),
                                  "ac": fmt_int(row.get("ac", 0)), "ad": fmt_int(row.get("ad", 0)),
                                  "af": row.get("af_pct", pct(row.get("af", 0), 3)),
                                  "cds": row.get("cds", ""),
                                  "cds_display": mutation_display(row.get("cds"), row.get("hgvsg")),
                                  "protein": row.get("protein", ""),
                                  "function": row.get("function", ""), "tier": row.get("tier", ""),
                                  "hgvsg_sample_count": row.get("hgvsg_sample_count", 0),
                                  "gene_case_count": row.get("gene_case_count", 0)}
                                 for row in variants],
                })
    except sqlite3.Error:
        return []
    return sorted(findings, key=lambda item: (-item["site_count"], item["cancer_name"]))


def build_cancer_coverage_rows(sqlite_db: Optional[Path], cancer_col: str = "cancer_type", hgvsg_col: str = "hgvsg", gene_col: str = "gene_symbol", mutation_table: str = "mutation") -> List[Dict[str, Any]]:
    """Return gene/site coverage for every configured cancer type, including zero rows."""
    counts: Dict[str, Dict[str, int]] = {}
    if sqlite_db and sqlite_db.exists():
        try:
            with sqlite3.connect(str(sqlite_db)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    f"SELECT {cancer_col}, COUNT(DISTINCT {gene_col}) AS gene_count, COUNT(DISTINCT {hgvsg_col}) AS site_count "
                    f"FROM {mutation_table} GROUP BY {cancer_col}"
                ).fetchall()
                counts = {safe_str(row[cancer_col], ""): {"gene_count": row["gene_count"], "site_count": row["site_count"]} for row in rows}
        except sqlite3.Error:
            pass
    rows = []
    for cancer_name, cancer_types in configured_cancer_groups():
        rows.append({"cancer_type": cancer_types[0], "cancer_name": cancer_name,
                     "gene_count": sum(counts.get(item, {}).get("gene_count", 0) for item in cancer_types),
                     "site_count": sum(counts.get(item, {}).get("site_count", 0) for item in cancer_types)})
    return rows


def build_risk_overview_rows(findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create the 2.2 table using the same per-cancer risk rule as chapter 3."""
    finding_by_type = {item["cancer_type"]: item for item in findings}
    rows = []
    for cancer_name, cancer_types in configured_cancer_groups():
        matched = [finding_by_type[item] for item in cancer_types if item in finding_by_type]
        site_count = sum(item["site_count"] for item in matched)
        risk_level = max((item["risk_level"] for item in matched), key=lambda value: {"低风险": 0, "中风险": 1, "高风险": 2}[value], default="阴")
        if site_count:
            # 数字标红加粗
            assessment = f"检出特异性突变点<span style='color:#e02424;font-weight:bold;'>{site_count}</span>个"
        else:
            assessment = "未检出特异性突变点"
        rows.append({
            "cancer_name": cancer_name.replace("癌", ""),
            "assessment": assessment,
            "risk_level": risk_level,
        })
    return rows


def load_variant_rows(variant_csv: Optional[Path]) -> List[Dict[str, Any]]:
    if not variant_csv:
        return []
    if not variant_csv.exists():
        raise FileNotFoundError(f"Variant CSV not found: {variant_csv}")

    rows: List[Dict[str, Any]] = []
    with variant_csv.open("r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            hgvsg = safe_str(row.get("HGVSG") or row.get("hgvsg") or row.get("HGVS") or row.get("variant") or row.get("ID"))
            if not hgvsg:
                continue
            ac = parse_int(row.get("AC") or row.get("ac") or row.get("alt_count") or 0)
            ad = parse_int(row.get("AD") or row.get("ad") or row.get("alt_depth") or 0)
            af_value = as_probability(row.get("AF") or row.get("af") or row.get("allele_frequency") or 0)
            cds = format_cds_hgvs(row.get("CDS") or row.get("cds") or "")
            protein = safe_str(row.get("PROTEIN") or row.get("protein") or "")
            function = safe_str(row.get("FUNCTION") or row.get("function") or "")
            gene = safe_str(row.get("GENE") or row.get("gene") or row.get("GENE_SYMBOL") or row.get("gene_symbol") or "")
            tier = safe_str(row.get("tier") or row.get("TIER") or row.get("Tier") or "")
            rows.append(
                {
                    "hgvsg": hgvsg,
                    "gene": gene,
                    "ac": ac,
                    "ad": ad,
                    "af": af_value,
                    "af_pct": pct(af_value, 3),
                    "cds": cds,
                    "cds_display": mutation_display(cds, hgvsg),
                    "protein": protein,
                    "function": function,
                    "tier": tier,
                }
            )
    return rows


def compute_variant_summary(variant_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not variant_rows:
        return {
            "variant_count": 0,
            "high_af_count": 0,
            "total_ac": 0,
            "total_ad": 0,
            "max_af": 0.0,
            "max_af_text": "0.000%",
        }

    high_af_count = sum(1 for row in variant_rows if row.get("af", 0.0) >= 0.01)
    max_af = max(row.get("af", 0.0) for row in variant_rows)
    total_ac = sum(parse_int(row.get("ac")) for row in variant_rows)
    total_ad = sum(parse_int(row.get("ad")) for row in variant_rows)
    return {
        "variant_count": len(variant_rows),
        "high_af_count": high_af_count,
        "total_ac": total_ac,
        "total_ad": total_ad,
        "max_af": max_af,
        "max_af_text": pct(max_af, 3),
    }


def build_report_data(input_path: Path, variant_csv: Optional[Path] = None) -> Dict[str, Any]:
    data = load_data(input_path, prob_csv=None, variant_csv=variant_csv)
    return normalize_data(data)


def load_data(
    input_path: Path,
    prob_csv: Optional[Path],
    sqlite_db: Optional[Path] = None,
    sqlite_query: Optional[str] = None,
    sqlite_cancer_type: Optional[str] = None,
    sqlite_cancer_col: str = "cancer_type",
    sqlite_sample_col: str = "cosmic_sample_id",
    sqlite_hgvsg_col: str = "hgvsg",
    sqlite_gene_col: str = "gene_symbol",
    variant_csv: Optional[Path] = None,
) -> Dict[str, Any]:
    with input_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if prob_csv:
        probs = []
        with prob_csv.open("r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                cancer = row.get("cancer") or row.get("癌种") or row.get("type") or row.get("name")
                value = row.get("risk_label") or row.get("风险等级") or row.get("风险") or row.get("probability") or row.get("概率") or row.get("score")
                if cancer and value is not None:
                    probs.append({"cancer": cancer, "risk_label": cancer_risk_label(value)})
        data.setdefault("result", {})["cancer_risks"] = probs

    variant_rows = load_variant_rows(variant_csv)
    if variant_rows:
        data["variants"] = variant_rows
        data["variant_summary"] = compute_variant_summary(variant_rows)

    if sqlite_db:
        cancer_match_summary = build_cancer_match_summary(
            sqlite_db=sqlite_db,
            hgvsgs=[row.get("hgvsg", "") for row in variant_rows],
            cancer_col=sqlite_cancer_col,
            hgvsg_col=sqlite_hgvsg_col,
        )
        if cancer_match_summary:
            data["cancer_match_summary"] = cancer_match_summary
            data.setdefault("cancer_match_summary_title", "按癌种的阳性位点数")
        data["cancer_findings"] = build_cancer_findings(
            sqlite_db=sqlite_db,
            variant_rows=variant_rows,
            cancer_col=sqlite_cancer_col,
            hgvsg_col=sqlite_hgvsg_col,
            sample_col=sqlite_sample_col,
            gene_col=sqlite_gene_col,
        )
        data["cancer_coverage_rows"] = build_cancer_coverage_rows(
            sqlite_db, sqlite_cancer_col, sqlite_hgvsg_col, sqlite_gene_col
        )
    return data


def normalize_data(raw: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(raw)
    meta = data.setdefault("report_meta", {})
    patient = data.setdefault("patient", {})
    sample = data.setdefault("sample", {})
    test = data.setdefault("test", {})
    result = data.setdefault("result", {})
    recommendation = data.setdefault("recommendation", {})

    meta.setdefault("brand_name", "Oncoseeing")
    meta.setdefault("lab_name", "IBIRI医学检验实验室")
    meta.setdefault("lab_name_en", "IBIRI Medical Laboratory")
    meta.setdefault("report_date", date.today().isoformat())
    sample_id = safe_str(sample.get("sample_id"), "CF2026-0724-0888")
    meta.setdefault("report_number", f"OS-{sample_id}")
    meta.setdefault("method_name", "cfDNA 突变检测技术")
    meta.setdefault("review_doctor", "________")
    meta.setdefault("issue_doctor", "________")
    meta.setdefault("sign_date", meta.get("report_date"))

    patient.setdefault("name", "张××")
    patient.setdefault("sex", "男")
    patient.setdefault("age", 52)
    patient.setdefault("hospital", "IBIRI健康管理中心")

    sample.setdefault("sample_id", sample_id)
    sample.setdefault("sample_type", "外周血（10mL，游离 DNA 保存管）")
    sample.setdefault("submitting_unit", patient.get("hospital", "IBIRI健康管理中心"))
    sample.setdefault("received_date", "2026-07-20")

    test.setdefault("cover_title_line1", "多癌种早期筛查检测与")
    test.setdefault("cover_title_line2", "风险评估报告")
    test.setdefault("report_subtitle", "基于循环游离DNA突变检测技术")
    test.setdefault("english_title", "Report on the Detection and Risk Assessment of Multi-Cancer Early Screening")

    cancer_risks = result.get("cancer_risks") or result.get("tissue_probabilities") or result.get("cancer_probabilities") or []
    if isinstance(cancer_risks, dict):
        cancer_risks = [{"cancer": cancer, "risk_label": label} for cancer, label in cancer_risks.items()]
    if not cancer_risks:
        cancer_risks = [
            {"cancer": "结直肠癌", "risk_label": "高"},
            {"cancer": "胃癌", "risk_label": "低"},
            {"cancer": "胰腺癌", "risk_label": "低"},
            {"cancer": "其他", "risk_label": "低"},
        ]
    result["cancer_risks"] = [{
        "cancer": safe_str(item.get("cancer") or item.get("name") or item.get("type")),
        "risk_label": cancer_risk_label(item.get("risk_label", item.get("probability", item.get("risk", "低")))),
    } for item in cancer_risks]
    result.pop("tissue_probabilities", None)
    result.pop("cancer_probabilities", None)

    variant_rows = data.get("variants") or []
    variant_summary = data.get("variant_summary") or compute_variant_summary(variant_rows)
    data["summary"] = variant_summary
    data["variants"] = variant_rows
    data["cancer_match_summary"] = data.get("cancer_match_summary") or []
    data["cancer_findings"] = data.get("cancer_findings") or []
    data["cancer_coverage_rows"] = data.get("cancer_coverage_rows") or build_cancer_coverage_rows(None)
    data["risk_overview_rows"] = build_risk_overview_rows(data["cancer_findings"])
    data["variant_table_rows"] = [
        {
            "hgvsg": row.get("hgvsg", "-"),
            "cds_display": mutation_display(row.get("cds"), row.get("hgvsg")),
            "ac": fmt_int(row.get("ac", 0)),
            "ad": fmt_int(row.get("ad", 0)),
            "af": row.get("af_pct", pct(row.get("af", 0), 3)),
        }
        for row in variant_rows
    ]

    has_cancer_match = bool(data.get("cancer_findings"))
    # 癌症信号以是否真正匹配到癌种为准（忽略输入中写死的布尔值）
    signal_text = "阳性" if has_cancer_match else "阴性"
    result["cancer_signal_text"] = signal_text
    # 总体风险等级 = 各癌种风险等级中的最高等级（高 > 中 > 低）；无癌种匹配时为“低风险”
    rank = {"低风险": 0, "中风险": 1, "高风险": 2}
    overall_level = "低风险"
    for finding in data.get("cancer_findings", []):
        level = safe_str(finding.get("risk_level"), "低风险")
        if rank.get(level, 0) > rank.get(overall_level, 0):
            overall_level = level
    result["risk_level"] = overall_level
    result["overall_probability_text"] = overall_level

    if not recommendation.get("items"):
        recommendation["items"] = [
            {"title": "建议一：尽快专科复诊", "detail": "建议尽快前往消化内科或肿瘤专科门诊复诊，结合既往病史、家族史及当前症状进行风险复核。"},
            {"title": "建议二：开展针对性检查", "detail": "优先考虑结肠镜检查，并根据临床需要结合腹部增强 CT / MRI、粪便潜血、CEA 等进一步评估。"},
            {"title": "建议三：动态随访", "detail": "如首次影像学或内镜检查未发现明确异常，建议结合临床判断在 1–3 个月内复查或补充相关检查。"},
        ]

    matched_genes = sorted({
        row["gene"]
        for finding in data["cancer_findings"]
        for row in finding.get("gene_statistics", [])
        if safe_str(row.get("gene"), "")
    })
    matched_mutations = sorted({
        mutation_display(row.get("cds"), row.get("hgvsg"))
        for finding in data["cancer_findings"]
        for row in finding.get("variants", [])
        if safe_str(row.get("hgvsg"), "")
    })
    matched_cancers = sorted({
        safe_str(finding.get("cancer_name"), "")
        for finding in data["cancer_findings"]
        if safe_str(finding.get("cancer_name"), "")
    })
    result["conclusion"] = result.get("conclusion") or (
        f"检测结果显示，您身体内目前出现了有癌化趋势的细胞，这些细胞的"
        f"{'、'.join(matched_genes)} 这 {len(matched_genes)} 个基因发生了"
        f"{'、'.join(matched_mutations)} 共 {len(matched_mutations)} 种突变，"
        f"这些突变是{'、'.join(matched_cancers)}的诱因。"
        if matched_genes and matched_mutations and matched_cancers else
        f"检测结果显示，本次 cfDNA 突变检测共检出 {variant_summary['variant_count']} 个变异位点；"
        f"当前未在已配置的数据库中匹配到可归因的癌种，建议结合临床检查结果进行进一步评估。"
    )

    result["interpretation_points"] = result.get("interpretation_points") or [
        f"输入的变异文件包含 {variant_summary['variant_count']} 个变异位点，覆盖了 AC、AD 与 AF 的关键信息。",
        f"其中 {variant_summary['high_af_count']} 个位点的 AF ≥ 1%，可作为高关注变异位点进行后续解读。",
        f"综合各癌种风险等级，本次总体风险等级判定为 {overall_level}。",
        "本报告应与病史、症状、实验室指标和影像学结果综合分析，不可单独作为疾病诊断结论。",
    ]

    data["disclaimer_items"] = data.get("disclaimer_items") or [
        "本报告仅作为多癌种早筛风险评估和癌种溯源辅助判断参考，不作为疾病诊断、治疗或保险理赔的唯一依据。",
        "检测结果受样本质量、个体生理状态、既往病史以及模型适用范围等因素影响，存在一定假阳性或假阴性风险。",
        "如报告提示中高风险或阳性，请尽快在专业医师指导下完成进一步检查；如提示低风险，亦不代表完全排除肿瘤存在。",
        "未经书面许可，不得对本报告进行删改、截取或用于与原始用途无关的商业宣传。",
    ]

    # Presentation fields
    data["sample_info_rows"] = [
        ("受检者", patient["name"]),
        ("样本编号", sample["sample_id"]),
        ("样本类型", sample["sample_type"]),
        ("性别", patient["sex"]),
        ("年龄", patient["age"]),
        ("送检单位", sample["submitting_unit"]),
        ("到样日期", sample["received_date"]),
        ("报告日期", meta["report_date"]),
    ]
    data["report_info_rows"] = [
        ("报告编号", meta["report_number"]),
        ("报告日期", meta["report_date"]),
        ("检测方法", meta["method_name"]),
        ("样本状态", "符合检测要求"),
    ]
    data["risk_cards"] = [
        {"title": "癌症信号", "value": signal_text, "sub": result["risk_level"], "class": "danger"},
        {"title": "检出位点", "value": f"{variant_summary['variant_count']}个", "sub": "特异突变点", "class": "primary"},
    ]
    return data


def build_cancer_bar_svg(distribution: List[Dict[str, Any]], median: float, customer_count: int) -> Markup:
    """Render a per-cancer, ≤30-site normalized mutation-count bar chart."""
    width, height = 560, 245
    x0, y0, chart_w, chart_h = 52, 182, 454, 122
    if not distribution:
        return Markup('<div class="empty-chart">暂无可用于绘制柱状图的数据库样本数据</div>')
    max_y = max(float(item["percentage"]) for item in distribution) or 1.0
    bar_width = max(5.0, min(18.0, chart_w / 32.0 - 2.0))
    bars = []
    for item in distribution:
        count = int(item["site_count"])
        percentage = float(item["percentage"])
        x = x0 + count / 30 * chart_w - bar_width / 2
        bar_height = percentage / max_y * chart_h
        y = y0 - bar_height
        bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" class="bar"/>')
    ticks = "".join(
        f'<text x="{x0 + value / 30 * chart_w:.1f}" y="{y0 + 20}" text-anchor="middle" class="tick">{value}</text>'
        for value in (0, 10, 20, 30)
    )
    median_x = x0 + min(median, 30) / 30 * chart_w
    customer_x = x0 + min(customer_count, 30) / 30 * chart_w
    svg = f'''<svg class="bar-chart" viewBox="0 0 {width} {height}" role="img" aria-label="癌种数据库突变位点数量分布">
      <line x1="{x0}" y1="{y0}" x2="{x0}" y2="{y0-chart_h}" class="axis"/>
      <line x1="{x0}" y1="{y0}" x2="{x0+chart_w}" y2="{y0}" class="axis"/>
      {''.join(bars)}
      <line x1="{median_x:.1f}" y1="{y0}" x2="{median_x:.1f}" y2="{y0-chart_h}" class="median-line"/>
      <line x1="{customer_x:.1f}" y1="{y0}" x2="{customer_x:.1f}" y2="{y0-chart_h}" class="customer-line"/>
      <text x="{median_x:.1f}" y="{y0-chart_h-8}" text-anchor="middle" class="median-label">中位数：{median:g}</text>
      <text x="{customer_x:.1f}" y="{y0-chart_h-24}" text-anchor="middle" class="customer-label">本次：{customer_count}</text>
      {ticks}
      <text x="{x0 + chart_w / 2:.1f}" y="{height-12}" text-anchor="middle" class="caption">癌症患者突变位点数量（最大截断至 30）</text>
      <text x="{x0-12}" y="{y0-chart_h}" text-anchor="end" class="tick">{max_y * 100:.1f}%</text>
      <text x="{x0-12}" y="{y0+3}" text-anchor="end" class="tick">0%</text>
    </svg>'''
    return Markup(svg)


def file_data_uri(path: Path, mime: str) -> str:
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"

def render_html(project_dir: Path, data: Dict[str, Any], output_html: Path) -> str:
    """Render report body and return the independently rendered PDF header."""
    env = Environment(
        loader=FileSystemLoader(str(project_dir / "templates")),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters["mixed"] = mixed_span
    env.filters["pct"] = pct
    env.globals["cancer_bar_svg"] = build_cancer_bar_svg
    env.globals["af_cell_range"] = af_cell_range
    env.globals["cancer_prevention_advice"] = lambda name: CANCER_PREVENTION_ADVICE.get(safe_str(name, ""), "")
    env.globals["supplement_sections"] = SUPPLEMENT_SECTIONS
    # 参考文献：编号按正文首次引用顺序自动生成（每次渲染前重置）
    citation_registry = CitationRegistry(REFERENCES)
    citation_registry.reset()
    env.filters["rich"] = make_rich_text(citation_registry)
    env.globals["cite"] = citation_registry.cite
    env.globals["references_used"] = citation_registry.used_references
    env.globals["format_reference"] = format_reference
    css = (project_dir / "static/css/report.css").read_text(encoding="utf-8")
    assets = {
        "cover": file_data_uri(project_dir / "static/assets/cover_background_right_bottom_v5_0.png", "image/png"),
        "logo": file_data_uri(project_dir / "static/assets/ibiri_full_logo_theme_v5_0.png", "image/png"),
        "header_bg": file_data_uri(project_dir / "static/assets/header_background_band_v5_0.png", "image/png"),
    }
    rendered = env.get_template("report.html").render(data=data, assets=assets, css=css)
    header_template = env.get_template("pdf_header.html").render(data=data, assets=assets)
    output_html.parent.mkdir(parents=True, exist_ok=True)
    output_html.write_text(rendered, encoding="utf-8")
    return header_template


def render_pdf(
    html_path: Path,
    output_pdf: Path,
    header_template: str,
    project_dir: Optional[Path] = None,
    paginated_html: Optional[Path] = None,
) -> Path:
    """把 HTML 打印成 PDF。

    分两步：
    1. 在 Chromium 中注入 static/js/paginate.js 做测量与切分，把跨页的
       业务容器拆成多个【独立完整闭合】的同类容器，并装进显式物理页
       .pdf-page；分页后的 DOM 序列化写出为 HTML（可直接检查/打印）。
    2. 用分页后的 DOM 直接打印 PDF，保证没有任何 DOM 容器跨物理页。
    """
    from playwright.sync_api import sync_playwright

    project_dir = project_dir or Path(__file__).resolve().parent
    paginate_js = (project_dir / "static/js/paginate.js").read_text(encoding="utf-8")
    if paginated_html is None:
        paginated_html = html_path.with_name(html_path.stem + ".paginated.html")

    with sync_playwright() as p:
        # browser = p.chromium.launch(headless=True, executable_path="/usr/bin/chromium")
        # 去掉executable_path，自动使用.cache里的chrome-headless-shell
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        page = browser.new_page(viewport={"width": 1240, "height": 1754}, device_scale_factor=1)
        page.emulate_media(media="print")
        page.set_content(html_path.read_text(encoding="utf-8"), wait_until="load")

        # 第一遍：测量 + 切分，就地改造 DOM
        page.add_script_tag(content=paginate_js)
        stats = page.evaluate("paginateReport()")

        # 导出分页后的 DOM（每页片段均为独立闭合容器）
        paginated = page.content()
        paginated_html.parent.mkdir(parents=True, exist_ok=True)
        paginated_html.write_text(paginated, encoding="utf-8")

        # 第二遍：基于分页后的 DOM 打印
        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        page.pdf(
            path=str(output_pdf),
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
            display_header_footer=True,
            header_template=header_template,
            footer_template="<div></div>",
        )
        browser.close()

    print(
        f"Paginated pages: {stats.get('pages', 0)} | "
        f"containers: {stats.get('containers', 0)} | "
        f"container splits: {stats.get('splits', 0)}"
    )
    return paginated_html


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Oncoseeing HTML/CSS report v5.0.1.")
    parser.add_argument("--input", "-i", required=True, help="Input JSON file.")
    parser.add_argument("--output", "-o", required=True, help="Output PDF file.")
    parser.add_argument("--prob-csv", default=None, help="Optional CSV to override cancer probabilities.")
    parser.add_argument("--variant-csv", default=None, help="Optional CSV containing HGVSG/AC/AD/AF values. Defaults to hgvsg_AD_AF_AC.csv next to the script.")
    parser.add_argument("--html", default=None, help="Optional output HTML path. Default: next to PDF.")
    parser.add_argument("--html-only", action="store_true", help="Only generate HTML, do not create PDF.")
    parser.add_argument("--sqlite-db", default=None, help="Optional SQLite database for mutation-site distribution data.")
    parser.add_argument("--sqlite-query", default=None, help="Optional SQL query returning cancer_type, cosmic_sample_id and hgvsg columns.")
    parser.add_argument("--sqlite-cancer-type", default=None, help="Optional cancer type filter to visualize.")
    parser.add_argument("--sqlite-cancer-col", default="cancer_type", help="Column name for the cancer type in the SQLite query result.")
    parser.add_argument("--sqlite-sample-col", default="cosmic_sample_id", help="Column name for the sample identifier in the SQLite query result.")
    parser.add_argument("--sqlite-hgvsg-col", default="hgvsg", help="Column name for the mutation identifier in the SQLite query result.")
    parser.add_argument("--sqlite-gene-col", default="gene_symbol", help="Column name for the gene symbol in the mutation table.")
    args = parser.parse_args()

    project_dir = Path(__file__).resolve().parent
    input_path = Path(args.input).resolve()
    prob_csv = Path(args.prob_csv).resolve() if args.prob_csv else None
    variant_csv = Path(args.variant_csv).resolve() if args.variant_csv else project_dir / "hgvsg_AD_AF_AC.csv"
    output_pdf = Path(args.output).resolve()
    output_html = Path(args.html).resolve() if args.html else output_pdf.with_suffix(".html")

    data = normalize_data(
        load_data(
            input_path,
            prob_csv,
            sqlite_db=Path(args.sqlite_db).resolve() if args.sqlite_db else None,
            sqlite_query=args.sqlite_query,
            sqlite_cancer_type=args.sqlite_cancer_type,
            sqlite_cancer_col=args.sqlite_cancer_col,
            sqlite_sample_col=args.sqlite_sample_col,
            sqlite_hgvsg_col=args.sqlite_hgvsg_col,
            sqlite_gene_col=args.sqlite_gene_col,
            variant_csv=variant_csv,
        )
    )
    header_template = render_html(project_dir, data, output_html)
    if not args.html_only:
        paginated_html = render_pdf(
            output_html, output_pdf, header_template, project_dir=project_dir
        )
    print(f"HTML written to: {output_html}")
    if not args.html_only:
        print(f"Paginated HTML written to: {paginated_html}")
        print(f"PDF written to: {output_pdf}")


if __name__ == "__main__":
    main()
