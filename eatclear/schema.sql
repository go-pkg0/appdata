-- EatClear 数据库结构
-- 设计原则：
--   1. 一开始就支持多国家/地区（jurisdiction: CN, EU, US, CODEX）
--   2. 每条规定都记录标准名称、版本、实施日期和原文出处，方便核查
--   3. verified = 1 表示已经对照官方原文核实；0 表示待核实，App 会显示提示
-- 修改结构时，请同时提高 data/build.py 里的 SCHEMA_VERSION。

PRAGMA foreign_keys = ON;

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 添加剂等物质。主键优先用 INS 编号（如 INS202），没有 INS 时用 CAS（如 CAS24634-61-5）
CREATE TABLE substances (
    id             TEXT PRIMARY KEY,
    ins            TEXT,
    cas            TEXT,
    e_number       TEXT,
    name_zh        TEXT NOT NULL,
    name_en        TEXT,
    function_class TEXT NOT NULL,   -- 功能类别，如 防腐剂、甜味剂、着色剂
    note           TEXT,
    -- 用途：additive 添加剂（GB 2760）、fortifier 营养强化剂（GB 14880），两者都是写 additive|fortifier
    roles          TEXT NOT NULL DEFAULT 'additive'
        CHECK (roles IN ('additive', 'fortifier', 'additive|fortifier'))
);

-- 别名：配料表上可能出现的各种写法，用于匹配 OCR 结果
-- alias_norm 是归一化后的写法（去空格、全角转半角、小写），由构建脚本生成
CREATE TABLE aliases (
    alias        TEXT NOT NULL,
    alias_norm   TEXT NOT NULL,
    substance_id TEXT NOT NULL REFERENCES substances(id),
    lang         TEXT NOT NULL DEFAULT 'zh',
    PRIMARY KEY (alias_norm, substance_id)
);
CREATE INDEX idx_aliases_norm ON aliases(alias_norm);

-- 各国的食品分类体系（中国为 GB 2760 附录 E 的食品分类号）
CREATE TABLE food_categories (
    jurisdiction TEXT NOT NULL,
    code         TEXT NOT NULL,
    name_zh      TEXT NOT NULL,
    name_en      TEXT,
    parent_code  TEXT,
    PRIMARY KEY (jurisdiction, code)
);

-- 添加剂使用限量
CREATE TABLE limits (
    substance_id       TEXT NOT NULL REFERENCES substances(id),
    jurisdiction       TEXT NOT NULL,
    standard           TEXT NOT NULL,   -- 如 GB 2760
    standard_version   TEXT NOT NULL,   -- 如 2024
    food_category_code TEXT NOT NULL,
    max_value          REAL,            -- NULL 表示"按生产需要适量使用"
    unit               TEXT,            -- 如 g/kg
    note               TEXT,            -- 如"以山梨酸计"
    source_ref         TEXT NOT NULL,   -- 原文出处，如"GB 2760-2024 表A.1"
    effective_from     TEXT,            -- YYYY-MM-DD
    verified           INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (substance_id, jurisdiction, standard_version, food_category_code)
);

-- 营养声称的判定规则（中国为 GB 28050）
CREATE TABLE claim_rules (
    jurisdiction     TEXT NOT NULL,
    claim_key        TEXT NOT NULL,     -- 如 sugar_free
    claim_name_zh    TEXT NOT NULL,     -- 如 无糖
    nutrient         TEXT NOT NULL,     -- 如 sugar
    comparator       TEXT NOT NULL CHECK (comparator IN ('<=', '>=')),
    threshold        REAL NOT NULL,
    unit             TEXT NOT NULL,     -- g, mg, kJ, %NRV
    basis            TEXT NOT NULL CHECK (basis IN ('per100g', 'per100ml')),
    standard         TEXT NOT NULL,
    standard_version TEXT NOT NULL,
    source_ref       TEXT NOT NULL,
    effective_from   TEXT,
    verified         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (jurisdiction, claim_key, basis, standard_version)
);

-- 识别得出、但不能用营养成分表数值对照的说法，附上给用户看的原因
-- comparative：比较类声称（如"减盐"），要和同类产品比，包装上的数字不够；
-- non_regulated：标准里没有对应数值定义的说法（如"0蔗糖"）
-- 同一个 claim_key 只能在 claim_rules 或 claim_notes 其中之一出现（由构建脚本检查）
CREATE TABLE claim_notes (
    jurisdiction  TEXT NOT NULL,
    claim_key     TEXT NOT NULL,
    claim_name_zh TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('comparative', 'non_regulated')),
    explain       TEXT NOT NULL,          -- 为什么无法对照，给用户看的一两句话
    PRIMARY KEY (jurisdiction, claim_key)
);

-- 包装上的说法和规则的对应关系，如 "0糖" "不含糖" -> sugar_free
CREATE TABLE claim_patterns (
    jurisdiction TEXT NOT NULL,
    pattern      TEXT NOT NULL,
    pattern_norm TEXT NOT NULL,
    claim_key    TEXT NOT NULL,
    PRIMARY KEY (jurisdiction, pattern_norm)
);

-- 营养素的中文名称和别名，用于识别营养成分表
CREATE TABLE nutrient_names (
    name      TEXT NOT NULL,
    name_norm TEXT PRIMARY KEY,
    nutrient  TEXT NOT NULL
);

-- 营养素参考值（NRV），用于 "高钙" 这类按 %NRV 判定的声称
CREATE TABLE nrv (
    jurisdiction     TEXT NOT NULL,
    nutrient         TEXT NOT NULL,
    value            REAL NOT NULL,
    unit             TEXT NOT NULL,
    standard         TEXT NOT NULL,
    standard_version TEXT NOT NULL,
    verified         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (jurisdiction, nutrient, standard_version)
);

-- "懂法" 分类，比如 食品标签、广告法
CREATE TABLE law_categories (
    id           TEXT PRIMARY KEY,
    jurisdiction TEXT NOT NULL,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL
);

-- "懂法" 科普文章
CREATE TABLE law_articles (
    id           TEXT PRIMARY KEY,
    jurisdiction TEXT NOT NULL,
    category     TEXT NOT NULL REFERENCES law_categories(id),
    sort_order   INTEGER NOT NULL DEFAULT 0,
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL,
    body_md      TEXT NOT NULL,
    updated_at   TEXT NOT NULL       -- 只有日期 YYYY-MM-DD，不换算时区
);

-- 文章末尾的原文链接（法律、标准原文的权威发布页面）
CREATE TABLE law_links (
    article_id   TEXT NOT NULL REFERENCES law_articles(id),
    sort_order   INTEGER NOT NULL,
    title        TEXT NOT NULL,
    url          TEXT NOT NULL,      -- https，域名在 build.py 的 LINK_DOMAINS 里
    PRIMARY KEY (article_id, sort_order)
);

-- 可调的数值设置，如一块方糖按多少克换算
CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    note  TEXT
);

-- 标签上固定的提示用语，用于识别致敏物质提示段、适用人群、注意事项、标准号、商品名等
-- phrase_norm 是归一化后的写法，由构建脚本生成
CREATE TABLE label_phrases (
    kind        TEXT NOT NULL CHECK (kind IN ('allergen_section', 'allergen_may_contain',
                                              'audience', 'notice', 'standard_code', 'product_name')),
    phrase      TEXT NOT NULL,
    phrase_norm TEXT NOT NULL,
    PRIMARY KEY (kind, phrase_norm)
);

-- 致敏物质分类（如 牛奶、鸡蛋、花生）
CREATE TABLE allergens (
    key        TEXT PRIMARY KEY,
    name_zh    TEXT NOT NULL,
    sort_order INTEGER NOT NULL
);

-- 致敏物质的各种写法
-- statement：出现在"致敏物质提示"里的写法；ingredient：出现在配料表里的写法
CREATE TABLE allergen_terms (
    term         TEXT NOT NULL,
    term_norm    TEXT NOT NULL,
    allergen_key TEXT NOT NULL REFERENCES allergens(key),
    kind         TEXT NOT NULL CHECK (kind IN ('statement', 'ingredient')),
    PRIMARY KEY (term_norm, kind)
);

-- 大白话说明：营养素、致敏物质、功能类别、添加剂的解释和小知识
-- subject_key：nutrient 为营养素 key，allergen 为致敏物质 key，function_class 为功能类别名，
-- substance 为物质 id，tip 为小知识自己的 id
CREATE TABLE notes (
    id           TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL CHECK (subject_type IN ('nutrient', 'allergen', 'function_class', 'substance', 'tip')),
    subject_key  TEXT NOT NULL,
    title        TEXT NOT NULL,
    body         TEXT NOT NULL,     -- 大白话说明，2~4 句
    verified     INTEGER NOT NULL DEFAULT 0,
    UNIQUE (subject_type, subject_key)
);

-- 说明的依据。tier 1：国标、膳食指南；2：WHO/IARC、JECFA、EFSA、FDA 等机构；3：期刊综述（只收 DOI 链接）
CREATE TABLE note_sources (
    note_id    TEXT NOT NULL REFERENCES notes(id),
    sort_order INTEGER NOT NULL,
    tier       INTEGER NOT NULL CHECK (tier IN (1, 2, 3)),
    org        TEXT NOT NULL,       -- 机构或期刊
    title      TEXT NOT NULL,       -- 文件名或论文标题
    year       INTEGER NOT NULL,
    url        TEXT NOT NULL,       -- https，域名在 build.py 的 TIER_DOMAINS 里
    method     TEXT NOT NULL,       -- 判定方式：一句话写清"拿什么和什么比"；只作说明时写"只作说明，不做数值对照"
    study_type TEXT,                -- 级别 3 必填：系统综述 / 荟萃分析
    is_dated   INTEGER NOT NULL DEFAULT 0,   -- 级别 3 且 构建年份 - year > 10
    verified   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (note_id, sort_order)
);
