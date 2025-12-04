"""
核心联赛配置 - 全球前100国家联赛分层管理
TIER_1: 五大联赛及主流高质量联赛（最优先）
TIER_2: 全球前100国家一级、二级联赛（扩展）
TIER_3: 国际赛事和杯赛
"""

# ========================================
# TIER_1: 核心顶级联赛（优先投注）
# ========================================
TIER_1_LEAGUES = {
    # === 五大联赛（最高优先级） ===
    'soccer-england-premier-league',           # 英超
    'soccer-spain-laliga',                     # 西甲
    'soccer-germany-bundesliga',               # 德甲
    'soccer-italy-serie-a',                    # 意甲
    'soccer-france-ligue-1',                   # 法甲

    # === 欧洲主流联赛 ===
    'soccer-netherlands-eredivisie',           # 荷甲
    'soccer-portugal-primeira-liga',           # 葡超
    'soccer-belgium-first-division-a',         # 比甲
    'soccer-turkey-super-lig',                 # 土超
    'soccer-scotland-premiership',             # 苏超
    'soccer-austria-bundesliga',               # 奥甲
    'soccer-switzerland-super-league',         # 瑞士超
    'soccer-greece-super-league-1',            # 希超
    'soccer-czech-republic-1-liga',            # 捷克甲
    'soccer-denmark-superligaen',              # 丹麦超
    'soccer-norway-eliteserien',               # 挪超
    'soccer-sweden-allsvenskan',               # 瑞典超
    'soccer-poland-ekstraklasa',               # 波兰甲
    'soccer-ukraine-premier-league',           # 乌超
    'soccer-croatia-1-hnl',                    # 克甲
    'soccer-serbia-superliga',                 # 塞超
    'soccer-romania-liga-i',                   # 罗甲

    # === 美洲主流联赛 ===
    'soccer-brazil-brasileiro-serie-a',        # 巴甲
    'soccer-argentina-superliga',              # 阿甲
    'soccer-mexico-primera-division-clausura', # 墨超
    'soccer-usa-major-league-soccer',          # 美职联
    'soccer-chile-primera-division',           # 智利甲
    'soccer-colombia-primera-a',               # 哥伦比亚甲
    'soccer-uruguay-primera-division',         # 乌拉圭甲
    'soccer-paraguay-primera-division-apertura', # 巴拉圭甲
    'soccer-ecuador-ligapro-primera-a',        # 厄瓜多尔甲

    # === 亚洲主流联赛 ===
    'soccer-japan-j-league',                   # 日职联
    'soccer-south-korea-k-league-1',           # 韩K联
    'soccer-china-super-league',               # 中超
    'soccer-saudi-arabia-saudi-prof-league',  # 沙特联
    'soccer-australia-a-league',               # 澳超
    'soccer-qatar-stars-league',               # 卡塔尔联
    'soccer-united-arab-emirates-arabian-gulf-league', # 阿联酋联
}

# ========================================
# TIER_2: 全球前100国家一级、二级联赛
# ========================================
TIER_2_LEAGUES = {
    # === 五大联赛次级 ===
    'soccer-england-championship',             # 英冠
    'soccer-spain-laliga-2',                   # 西乙
    'soccer-germany-2nd-bundesliga',           # 德乙
    'soccer-italy-serie-b',                    # 意乙
    'soccer-france-ligue-2',                   # 法乙

    # === 欧洲次级联赛 ===
    'soccer-netherlands-eerste-divisie',       # 荷乙
    'soccer-portugal-segunda-liga',            # 葡乙
    'soccer-belgium-first-division-b',         # 比乙
    'soccer-turkey-1-lig',                     # 土乙
    'soccer-scotland-championship',            # 苏冠
    'soccer-austria-2-liga',                   # 奥乙
    'soccer-switzerland-challenge-league',     # 瑞士挑战
    'soccer-greece-super-league-2',            # 希乙
    'soccer-czech-republic-2-liga',            # 捷克乙
    'soccer-denmark-1st-division',             # 丹麦甲
    'soccer-norway-obos-ligaen',               # 挪威甲
    'soccer-sweden-superettan',                # 瑞典甲
    'soccer-poland-1-liga',                    # 波兰乙
    'soccer-croatia-2-hnl',                    # 克乙
    'soccer-romania-liga-ii',                  # 罗乙

    # === 美洲次级联赛 ===
    'soccer-brazil-brasileiro-serie-b',        # 巴乙
    'soccer-argentina-primera-nacional',       # 阿乙
    'soccer-mexico-liga-de-ascenso-clausura',  # 墨乙
    'soccer-usa-usl-championship',             # 美冠联
    'soccer-chile-primera-b',                  # 智利乙
    'soccer-colombia-primera-b',               # 哥伦比亚乙
    'soccer-uruguay-segunda-division',         # 乌拉圭乙

    # === 亚洲次级联赛 ===
    'soccer-japan-j-league-2',                 # 日职乙
    'soccer-south-korea-k-league-2',           # 韩K2联
    'soccer-china-league-one',                 # 中甲
    'soccer-thailand-thai-league-1',           # 泰超
    'soccer-vietnam-v-league-1',               # 越南联
    'soccer-malaysia-super-league',            # 马来西亚超
    'soccer-indonesia-liga-1',                 # 印尼联
    'soccer-singapore-premier-league',         # 新加坡联
    'soccer-india-indian-super-league',        # 印度超
    'soccer-uzbekistan-pfl',                   # 乌兹别克联
    'soccer-iraq-iraqi-league',                # 伊拉克联
    'soccer-iran-persian-gulf-pro-league',     # 伊朗联

    # === 非洲联赛 ===
    'soccer-south-africa-t903e-premiership',   # 南非超
    'soccer-egypt-premier-league',             # 埃及超
    'soccer-morocco-botola-pro',               # 摩洛哥联
    'soccer-algeria-ligue-1',                  # 阿尔及利亚联
    'soccer-tunisia-ligue-1',                  # 突尼斯联
    'soccer-nigeria-npfl',                     # 尼日利亚联
    'soccer-ghana-premier-league',             # 加纳联
    'soccer-kenya-premier-league',             # 肯尼亚联
    'soccer-senegal-premier-league',           # 塞内加尔联
    'soccer-ivory-coast-ligue-1',              # 科特迪瓦联
    'soccer-cameroon-elite-one',               # 喀麦隆联
    'soccer-uganda-premier-league',            # 乌干达联
    'soccer-tanzania-premier-league',          # 坦桑尼亚联
    'soccer-zambia-super-league',              # 赞比亚联
    'soccer-zimbabwe-premier-league',          # 津巴布韦联
    'soccer-ethiopia-premier-league',          # 埃塞俄比亚联
    'soccer-rwanda-rwanda-premier-league',     # 卢旺达联

    # === 中东欧其他联赛 ===
    'soccer-russia-premier-league',            # 俄超
    'soccer-hungary-nb-i',                     # 匈牙利甲
    'soccer-slovakia-superliga',               # 斯洛伐克甲
    'soccer-slovenia-prvaliga',                # 斯洛文尼亚甲
    'soccer-bulgaria-first-professional-league', # 保加利亚甲
    'soccer-bosnia-herzegovina-premijer-liga', # 波黑甲
    'soccer-north-macedonia-first-league',     # 北马其顿甲
    'soccer-albania-kategoria-superiore',      # 阿尔巴尼亚甲
    'soccer-belarus-vysshaya-liga',            # 白俄罗斯甲
    'soccer-kazakhstan-premier-league',        # 哈萨克斯坦甲
    'soccer-azerbaijan-premier-league',        # 阿塞拜疆甲
    'soccer-georgia-erovnuli-liga',            # 格鲁吉亚甲
    'soccer-armenia-premier-league',           # 亚美尼亚甲
    'soccer-moldova-divizia-nationala',        # 摩尔多瓦甲
    'soccer-montenegro-1-cfl',                 # 黑山甲
    'soccer-cyprus-1st-division',              # 塞浦路斯甲
    'soccer-malta-premier-league',             # 马耳他甲

    # === 北欧波罗的海 ===
    'soccer-finland-veikkausliiga',            # 芬兰甲
    'soccer-iceland-t9038-besta-deild',        # 冰岛甲
    'soccer-estonia-premium-liiga',            # 爱沙尼亚甲
    'soccer-latvia-virsliga',                  # 拉脱维亚甲
    'soccer-lithuania-a-lyga',                 # 立陶宛甲
    'soccer-faroe-islands-1st-deild',          # 法罗群岛甲

    # === 北美中美加勒比 ===
    'soccer-canada-canadian-premier-league',   # 加拿大超
    'soccer-costa-rica-primera-division-clausura', # 哥斯达黎加甲
    'soccer-panama-liga-panamena-de-futbol-apertura', # 巴拿马甲
    'soccer-honduras-liga-nacional-clausura',  # 洪都拉斯甲
    'soccer-el-salvador-primera-division',     # 萨尔瓦多甲
    'soccer-guatemala-liga-nacional-clausura', # 危地马拉甲
    'soccer-jamaica-premier-league',           # 牙买加超
    'soccer-trinidad-and-tobago-premier-league', # 特立尼达联

    # === 南美其他 ===
    'soccer-peru-primera-division',            # 秘鲁甲
    'soccer-bolivia-division-profesional',     # 玻利维亚甲
    'soccer-venezuela-primera-division',       # 委内瑞拉甲

    # === 大洋洲 ===
    'soccer-new-zealand-premiership',          # 新西兰超

    # === 其他欧洲小联赛 ===
    'soccer-ireland-premier-division',         # 爱尔兰超
    'soccer-northern-ireland-premiership',     # 北爱尔兰超
    'soccer-wales-premier-league',             # 威尔士超
    'soccer-luxembourg-promotion-d-honneur',   # 卢森堡甲
    'soccer-andorra-primera-divisio',          # 安道尔甲
    'soccer-san-marino-campionato-sammarinese', # 圣马力诺联

    # === 亚洲其他 ===
    'soccer-philippines-philippines-footb-league', # 菲律宾联
    'soccer-myanmar-national-league',          # 缅甸联
    'soccer-hong-kong-premier-league',         # 香港超
    'soccer-taiwan-football-premier-league',   # 台湾联
    'soccer-macau-football-league',            # 澳门联
    'soccer-bangladesh-premier-league',        # 孟加拉联
    'soccer-pakistan-premier-league',          # 巴基斯坦联
    'soccer-kyrgyzstan-top-league',            # 吉尔吉斯联
    'soccer-tajikistan-vysshaya-liga',         # 塔吉克斯坦联
    'soccer-turkmenistan-yokary-liga',         # 土库曼斯坦联
    'soccer-afghanistan-premier-league',       # 阿富汗联
    'soccer-syria-premier-league',             # 叙利亚联
    'soccer-lebanon-premier-league',           # 黎巴嫩联
    'soccer-jordan-pro-league',                # 约旦联
    'soccer-bahrain-premier-league',           # 巴林联
    'soccer-oman-professional-league',         # 阿曼联
    'soccer-kuwait-premier-league',            # 科威特联
    'soccer-yemen-yemeni-league',              # 也门联
}

# ========================================
# TIER_3: 国际赛事和杯赛
# ========================================
TIER_3_INTERNATIONAL = {
    # === 欧洲国际赛事 ===
    'soccer-international-clubs-uefa-champions-league',  # 欧冠
    'soccer-international-clubs-uefa-europa-league',     # 欧联
    'soccer-international-clubs-uefa-conference-league', # 欧协联
    'soccer-international-clubs-uefa-super-cup',         # 欧超杯

    # === 南美国际赛事 ===
    'soccer-international-clubs-copa-libertadores',      # 南美解放者杯
    'soccer-international-clubs-copa-sudamericana',      # 南美杯
    'soccer-international-clubs-recopa-sudamericana',    # 南美优胜者杯

    # === 国家队赛事 ===
    'soccer-international-world-cup',                    # 世界杯
    'soccer-international-wc-qualifying-conmebol',       # 南美世预赛
    'soccer-international-wc-qualifying-uefa',           # 欧洲世预赛
    'soccer-international-wc-qualifying-afc',            # 亚洲世预赛
    'soccer-international-wc-qualifying-caf',            # 非洲世预赛
    'soccer-international-wc-qualifying-concacaf',       # 中北美世预赛
    'soccer-international-uefa-euro',                    # 欧洲杯
    'soccer-international-copa-america',                 # 美洲杯
    'soccer-international-afc-asian-cup',                # 亚洲杯
    'soccer-international-africa-cup-of-nations',        # 非洲杯
    'soccer-international-gold-cup',                     # 金杯赛
    'soccer-international-uefa-nations-league',          # 欧国联
    'soccer-international-friendlies',                   # 国际友谊赛

    # === 国内杯赛（主要国家） ===
    'soccer-england-fa-cup',                             # 英足总杯
    'soccer-england-efl-cup',                            # 英联杯
    'soccer-spain-copa-del-rey',                         # 国王杯
    'soccer-germany-dfb-pokal',                          # 德国杯
    'soccer-italy-coppa-italia',                         # 意大利杯
    'soccer-france-coupe-de-france',                     # 法国杯
    'soccer-portugal-taca-de-portugal',                  # 葡萄牙杯
    'soccer-netherlands-knvb-beker',                     # 荷兰杯
    'soccer-brazil-copa-do-brasil',                      # 巴西杯
    'soccer-argentina-copa-argentina',                   # 阿根廷杯
    'soccer-mexico-copa-mx',                             # 墨西哥杯
}

# ========================================
# 联赛评分（用于多场候选时的排序）
# ========================================
LEAGUE_SCORES = {
    # === 五大联赛（10分） ===
    'soccer-england-premier-league': 10,
    'soccer-spain-laliga': 10,
    'soccer-germany-bundesliga': 10,
    'soccer-italy-serie-a': 10,
    'soccer-france-ligue-1': 10,

    # === 欧洲次级五大联赛（9分） ===
    'soccer-england-championship': 9,
    'soccer-spain-laliga-2': 9,
    'soccer-germany-2nd-bundesliga': 9,
    'soccer-italy-serie-b': 9,
    'soccer-france-ligue-2': 9,

    # === 欧洲主流联赛（8-9分） ===
    'soccer-netherlands-eredivisie': 9,
    'soccer-portugal-primeira-liga': 9,
    'soccer-belgium-first-division-a': 8,
    'soccer-turkey-super-lig': 8,
    'soccer-scotland-premiership': 8,
    'soccer-austria-bundesliga': 8,
    'soccer-switzerland-super-league': 8,
    'soccer-greece-super-league-1': 7,
    'soccer-czech-republic-1-liga': 7,
    'soccer-denmark-superligaen': 7,
    'soccer-norway-eliteserien': 7,
    'soccer-sweden-allsvenskan': 7,
    'soccer-poland-ekstraklasa': 7,
    'soccer-ukraine-premier-league': 7,

    # === 美洲主流联赛（7-9分） ===
    'soccer-brazil-brasileiro-serie-a': 9,
    'soccer-argentina-superliga': 8,
    'soccer-mexico-primera-division-clausura': 8,
    'soccer-usa-major-league-soccer': 7,
    'soccer-chile-primera-division': 7,
    'soccer-colombia-primera-a': 7,

    # === 亚洲主流联赛（7-8分） ===
    'soccer-japan-j-league': 8,
    'soccer-south-korea-k-league-1': 8,
    'soccer-china-super-league': 7,
    'soccer-saudi-arabia-saudi-prof-league': 7,
    'soccer-australia-a-league': 7,

    # === 国际赛事（9-10分） ===
    'soccer-international-clubs-uefa-champions-league': 10,
    'soccer-international-clubs-uefa-europa-league': 9,
    'soccer-international-clubs-copa-libertadores': 9,
    'soccer-international-world-cup': 10,
    'soccer-international-uefa-euro': 10,
    'soccer-international-copa-america': 9,
}

# ========================================
# 联赛集合定义
# ========================================

# 默认使用 TIER_1（最严格）
CORE_LEAGUES = TIER_1_LEAGUES

# TIER_1 + TIER_2（扩展模式）
EXPANDED_LEAGUES = TIER_1_LEAGUES | TIER_2_LEAGUES

# 全部联赛（包括国际赛事）
ALL_LEAGUES = TIER_1_LEAGUES | TIER_2_LEAGUES | TIER_3_INTERNATIONAL

# ========================================
# 辅助函数
# ========================================

def get_league_score(league_key):
    """
    获取联赛评分

    返回:
        score: 评分（0-10），未知联赛返回0
    """
    return LEAGUE_SCORES.get(league_key, 0)


def get_league_tier(league_key):
    """
    获取联赛等级

    返回:
        tier: 1/2/3，未知返回0
    """
    if league_key in TIER_1_LEAGUES:
        return 1
    elif league_key in TIER_2_LEAGUES:
        return 2
    elif league_key in TIER_3_INTERNATIONAL:
        return 3
    else:
        return 0


def is_top_league(league_key):
    """检查是否为顶级联赛（五大联赛）"""
    top_5 = {
        'soccer-england-premier-league',
        'soccer-spain-laliga',
        'soccer-germany-bundesliga',
        'soccer-italy-serie-a',
        'soccer-france-ligue-1',
    }
    return league_key in top_5


# ========================================
# 统计信息
# ========================================
def print_league_stats():
    """打印联赛统计信息"""
    print("="*60)
    print("联赛配置统计")
    print("="*60)
    print(f"TIER_1 核心联赛: {len(TIER_1_LEAGUES)} 个")
    print(f"TIER_2 扩展联赛: {len(TIER_2_LEAGUES)} 个")
    print(f"TIER_3 国际赛事: {len(TIER_3_INTERNATIONAL)} 个")
    print(f"总计: {len(ALL_LEAGUES)} 个")
    print(f"有评分的联赛: {len(LEAGUE_SCORES)} 个")
    print("="*60)


if __name__ == '__main__':
    print_league_stats()

    print("\n五大联赛:")
    for league in ['soccer-england-premier-league', 'soccer-spain-laliga',
                   'soccer-germany-bundesliga', 'soccer-italy-serie-a',
                   'soccer-france-ligue-1']:
        print(f"  {league}: {get_league_score(league)}分, TIER-{get_league_tier(league)}")
