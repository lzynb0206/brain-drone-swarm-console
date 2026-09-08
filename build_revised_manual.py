from pathlib import Path
from docx import Document
from docx.shared import Inches, Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.section import WD_SECTION
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

ROOT = Path('/private/tmp/brain_drone_manual_assets')
OUT_DIR = Path('/Users/geniusalu/Documents/brain-drone-swarm-console/deliverables')
OUT = OUT_DIR / '脑控无人机蜂群作战系统_操作手册_修订版.docx'
OUT_DIR.mkdir(parents=True, exist_ok=True)

# macOS/LibreOffice exposes the built-in Simplified Chinese Song typeface as
# Songti SC. It is the platform's 宋体-family font and renders reliably here.
FONT = 'Songti SC'
BLACK = RGBColor(0, 0, 0)
GRAY = 'BFBFBF'


def set_run_font(run, size=None, bold=None):
    run.font.name = FONT
    run._element.rPr.rFonts.set(qn('w:eastAsia'), FONT)
    run.font.color.rgb = BLACK
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold


def set_cell_border(cell, color=GRAY, size='6'):
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = tc_pr.first_child_found_in('w:tcBorders')
    if borders is None:
        borders = OxmlElement('w:tcBorders')
        tc_pr.append(borders)
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        tag = 'w:' + edge
        el = borders.find(qn(tag))
        if el is None:
            el = OxmlElement(tag)
            borders.append(el)
        el.set(qn('w:val'), 'single')
        el.set(qn('w:sz'), size)
        el.set(qn('w:color'), color)


def set_cell_margins(cell, top=110, start=110, bottom=110, end=110):
    tc = cell._tc
    tc_pr = tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in('w:tcMar')
    if tc_mar is None:
        tc_mar = OxmlElement('w:tcMar')
        tc_pr.append(tc_mar)
    for m, v in (('top', top), ('start', start), ('bottom', bottom), ('end', end)):
        node = tc_mar.find(qn(f'w:{m}'))
        if node is None:
            node = OxmlElement(f'w:{m}')
            tc_mar.append(node)
        node.set(qn('w:w'), str(v))
        node.set(qn('w:type'), 'dxa')


def add_page_number(paragraph):
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    fld_char1 = OxmlElement('w:fldChar')
    fld_char1.set(qn('w:fldCharType'), 'begin')
    instr = OxmlElement('w:instrText')
    instr.set(qn('xml:space'), 'preserve')
    instr.text = ' PAGE '
    fld_char2 = OxmlElement('w:fldChar')
    fld_char2.set(qn('w:fldCharType'), 'end')
    run._r.extend([fld_char1, instr, fld_char2])
    set_run_font(run, 9)


doc = Document()
section = doc.sections[0]
section.page_width = Cm(21)
section.page_height = Cm(29.7)
section.top_margin = Cm(2.1)
section.bottom_margin = Cm(1.8)
section.left_margin = Cm(2.2)
section.right_margin = Cm(2.2)

styles = doc.styles
normal = styles['Normal']
normal.font.name = FONT
normal._element.rPr.rFonts.set(qn('w:eastAsia'), FONT)
normal.font.size = Pt(11)
normal.font.color.rgb = BLACK
normal.paragraph_format.line_spacing = 1.45
normal.paragraph_format.space_after = Pt(6)

for name, size, before, after in (
    ('Title', 26, 0, 16),
    ('Heading 1', 18, 0, 12),
    ('Heading 2', 14, 10, 7),
    ('Heading 3', 12, 8, 5),
):
    st = styles[name]
    st.font.name = FONT
    st._element.rPr.rFonts.set(qn('w:eastAsia'), FONT)
    st.font.size = Pt(size)
    st.font.bold = True
    st.font.color.rgb = BLACK
    st.paragraph_format.space_before = Pt(before)
    st.paragraph_format.space_after = Pt(after)
    st.paragraph_format.keep_with_next = True

# Remove the decorative rule inherited by some Word Title style templates.
title_ppr = styles['Title']._element.get_or_add_pPr()
title_border = title_ppr.find(qn('w:pBdr'))
if title_border is not None:
    title_ppr.remove(title_border)

if '图题' not in styles:
    cap = styles.add_style('图题', WD_STYLE_TYPE.PARAGRAPH)
else:
    cap = styles['图题']
cap.font.name = FONT
cap._element.rPr.rFonts.set(qn('w:eastAsia'), FONT)
cap.font.size = Pt(9)
cap.font.color.rgb = BLACK
cap.paragraph_format.space_before = Pt(4)
cap.paragraph_format.space_after = Pt(8)
cap.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
cap.paragraph_format.keep_with_next = True

header = section.header.paragraphs[0]
header.text = '脑控无人机蜂群作战系统 V1.0 操作手册'
header.alignment = WD_ALIGN_PARAGRAPH.RIGHT
for run in header.runs:
    set_run_font(run, 9)
add_page_number(section.footer.paragraphs[0])


def p(text='', style=None, align=None, bold_lead=None):
    para = doc.add_paragraph(style=style)
    if bold_lead and text.startswith(bold_lead):
        r1 = para.add_run(bold_lead)
        set_run_font(r1, bold=True)
        r2 = para.add_run(text[len(bold_lead):])
        set_run_font(r2)
    else:
        r = para.add_run(text)
        set_run_font(r)
    if align is not None:
        para.alignment = align
    return para


def bullets(items, numbered=False):
    style = 'List Number' if numbered else 'List Bullet'
    for item in items:
        para = doc.add_paragraph(style=style)
        r = para.add_run(item)
        set_run_font(r)
        para.paragraph_format.left_indent = Cm(0.75)
        para.paragraph_format.first_line_indent = Cm(-0.35)
        para.paragraph_format.space_after = Pt(4)


def table(headers, rows, widths=None):
    t = doc.add_table(rows=1, cols=len(headers))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for idx, text in enumerate(headers):
        cell = t.rows[0].cells[idx]
        cell.text = text
        if widths:
            cell.width = Cm(widths[idx])
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        set_cell_border(cell)
        set_cell_margins(cell)
        for para in cell.paragraphs:
            para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            para.paragraph_format.space_after = Pt(0)
            for run in para.runs:
                set_run_font(run, 10, True)
    for ridx, row in enumerate(rows):
        cells = t.add_row().cells
        for idx, text in enumerate(row):
            cells[idx].text = text
            if widths:
                cells[idx].width = Cm(widths[idx])
            cells[idx].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            set_cell_border(cells[idx])
            set_cell_margins(cells[idx])
            for para in cells[idx].paragraphs:
                para.paragraph_format.space_after = Pt(0)
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER if idx == 0 else WD_ALIGN_PARAGRAPH.LEFT
                for run in para.runs:
                    set_run_font(run, 9.5)
    doc.add_paragraph().paragraph_format.space_after = Pt(0)
    return t


def figure(path, caption, width=6.35):
    para = doc.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    para.paragraph_format.space_before = Pt(4)
    para.paragraph_format.space_after = Pt(0)
    para.paragraph_format.keep_with_next = True
    run = para.add_run()
    run.add_picture(str(path), width=Inches(width))
    cp = doc.add_paragraph(caption, style='图题')
    for run in cp.runs:
        set_run_font(run, 9)


def page_break():
    doc.add_page_break()


def chapter(title):
    p(title, 'Heading 1')


# Cover
doc.add_paragraph().paragraph_format.space_after = Pt(58)
title = p('脑控无人机蜂群作战系统', 'Title', WD_ALIGN_PARAGRAPH.CENTER)
p('V1.0 操作手册', None, WD_ALIGN_PARAGRAPH.CENTER)
for run in doc.paragraphs[-1].runs:
    set_run_font(run, 20, True)
doc.add_paragraph().paragraph_format.space_after = Pt(80)
p('适用对象  作战操作员  科研人员  系统工程师  系统管理员', None, WD_ALIGN_PARAGRAPH.CENTER)
p('文档版本  V1.1', None, WD_ALIGN_PARAGRAPH.CENTER)
p('修订日期  2026 年 9 月', None, WD_ALIGN_PARAGRAPH.CENTER)
doc.add_paragraph().paragraph_format.space_after = Pt(105)
p('本系统为科研演示原型  请在授权环境中使用', None, WD_ALIGN_PARAGRAPH.CENTER)

page_break()
chapter('文件修订记录')
table(
    ['版本', '日期', '修订内容', '编制'],
    [
        ['V1.0', '2026-09-03', '形成桌面端、认证后端与数据库演示版本', '项目组'],
        ['V1.1', '2026-09-07', '按实际界面重编操作流程，补充科研、作战、工程和管理功能截图', '项目组'],
    ],
    [2.0, 3.0, 8.0, 2.0],
)
p('阅读说明', 'Heading 2')
p('本手册以当前演示版本界面为依据，说明各角色可见功能、基本操作和预期反馈。界面截图中的设备状态、脑电波形、系统指标和作战态势均用于演示，实际部署时应以接入的数据源、飞控网关和权限策略为准。')
p('界面约定', 'Heading 2')
table(
    ['界面元素', '含义与使用方法'],
    [
        ['顶部导航', '切换科研工作台的 EEG、范式、算法、控制、映射和数据模块。'],
        ['左侧功能菜单', '切换系统工程师工作台的目标识别、设备状态、信号质量、数据管理和诊断页面。'],
        ['状态灯与提示条', '绿色通常表示连接或运行正常；黄色表示警告；红色表示异常、断开或急停。'],
        ['按钮', '点击后观察页面状态、运行日志或底部提示，确认操作已被系统接受。'],
    ],
    [3.2, 11.8],
)

page_break()
chapter('目录')
toc_items = [
    ('前言', 4, 0),
    ('1 系统概述', 5, 0),
    ('2 使用准备与登录', 6, 0),
    ('2.1 运行前检查', 6, 1),
    ('2.2 登录和角色进入', 7, 1),
    ('2.3 账号与会话管理', 8, 1),
    ('3 科研人员工作台', 9, 0),
    ('3.1 EEG 实时监测', 9, 1),
    ('3.2 范式参数配置', 10, 1),
    ('3.3 算法与控制配置', 11, 1),
    ('3.4 实验数据回看', 12, 1),
    ('4 作战操作员工作台', 13, 0),
    ('4.1 蜂群态势查看', 13, 1),
    ('4.2 手动控制', 14, 1),
    ('4.3 脑状态与紧急停止', 15, 1),
    ('5 系统工程师工作台', 16, 0),
    ('5.1 目标与设备状态', 16, 1),
    ('5.2 信号质量监控', 17, 1),
    ('5.3 数据管理与深度诊断', 18, 1),
    ('6 系统管理员工作台', 19, 0),
    ('7 常见问题与安全要求', 20, 0),
    ('附录 A 快速检查清单', 21, 0),
]
for text, page, level in toc_items:
    para = doc.add_paragraph()
    para.paragraph_format.left_indent = Cm(0.65 if level else 0)
    para.paragraph_format.space_after = Pt(3 if level else 6)
    para.paragraph_format.tab_stops.add_tab_stop(Cm(15.5), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
    r = para.add_run(f'{text}\t{page}')
    set_run_font(r, 10.5 if level else 11, False if level else True)

page_break()
chapter('前言')
p('本手册用于指导用户完成脑控无人机蜂群作战系统的登录、功能浏览、科研参数配置、蜂群演示控制、系统监控和后台管理。建议首次使用时先阅读第 1、2 章，再根据账号角色进入对应章节。')
p('系统当前定位', 'Heading 2')
p('当前版本是一套桌面上位机科研演示原型。账号注册、身份登录和角色信息由后端服务保存；EEG 曲线、算法指标、蜂群状态、运行日志和控制反馈主要由演示数据源提供。界面可以完整展示操作链路，但不等同于已接入真实脑电采集设备和真实无人机。')
p('重要安全说明', 'Heading 2')
bullets([
    '未完成飞控安全仲裁、通信链路保护和现场风险评估前，不得将本系统用于真实无人机作战或其他高风险控制。',
    '紧急停止、身份鉴权、指令审计和失联保护应在可信后端或飞控侧重复执行，不能只依赖桌面界面。',
    '演示过程中如出现状态异常，应先停止任务，再检查设备连接、日志和数据源。',
])
p('截图说明', 'Heading 2')
p('截图来自所提供的系统录屏和现有手册，保留软件本身的深色界面配色；文档页面和说明文字统一采用白底、黑字、宋体。')

page_break()
chapter('1 系统概述')
p('脑控无人机蜂群作战系统按用户身份显示不同工作台，使科研配置、作战演示、工程监控和系统管理互相分离。登录成功后，系统读取账号角色并进入对应首页。')
table(
    ['角色', '主要功能', '建议阅读'],
    [
        ['科研人员', '查看 EEG 波形；配置 SSVEP、MI 或混合范式；选择算法；设置控制参数；回看实验数据。', '第 3 章'],
        ['作战操作员', '查看蜂群态势、目标状态、链路状态与脑控识别结果；执行方向控制和紧急停止。', '第 4 章'],
        ['系统工程师', '查看目标识别、设备状态、信号质量、实验数据、运行日志与深度诊断。', '第 5 章'],
        ['系统管理员', '查看设备、用户、存储和备份状态，执行授权范围内的管理操作。', '第 6 章'],
    ],
    [2.5, 9.5, 3.0],
)
p('典型操作顺序', 'Heading 2')
bullets([
    '启动后端服务和桌面客户端，确认本机网络及认证服务可访问。',
    '使用已授权账号登录，核对进入的角色工作台是否正确。',
    '先确认设备、数据源和系统状态，再开展实验配置或作战演示。',
    '操作后查看提示条、日志或状态灯，确认系统已产生预期反馈。',
    '完成后停止任务并安全退出账号。',
], numbered=True)

page_break()
chapter('2 使用准备与登录')
p('2.1 运行前检查', 'Heading 2')
table(
    ['检查项', '要求'],
    [
        ['显示与内存', '建议使用 1920×1080 或更高分辨率；客户端和后端分别预留 4 GB 以上内存。'],
        ['后端与数据库', '认证服务已启动；PostgreSQL 可用；客户端能够访问配置的后端地址和端口。'],
        ['演示设备', '需要演示设备状态时，确认 Mock 数据源或相应适配器已启用。'],
        ['账号权限', '使用与工作内容对应的账号；正式部署中高权限角色应由管理员授权。'],
    ],
    [3.2, 11.8],
)
p('2.2 登录和角色进入', 'Heading 2')
figure(ROOT / 'original_media/image2.jpeg', '图 2-1 登录界面')
bullets([
    '在邮箱框输入已注册邮箱，在密码框输入账号密码。',
    '点击“登录”，等待系统完成身份校验。',
    '登录成功后核对顶部标题、菜单和工作台内容；若角色不符，请退出并联系管理员检查授权。',
])

page_break()
p('2.3 账号与会话管理', 'Heading 2')
p('新用户可从登录页进入注册流程，填写邮箱、显示名称和密码。演示版本可能提供角色选择；正式环境应关闭高权限角色的自助选择，由管理员统一授权。')
p('登录结果', 'Heading 3')
table(
    ['现象', '处理方法'],
    [
        ['提示无法连接认证服务', '确认后端服务已启动，检查地址、端口和本机网络。'],
        ['提示账号或密码错误', '检查邮箱拼写、大小写和密码；仍无法登录时联系管理员重置。'],
        ['登录后进入错误工作台', '退出账号，由管理员核对用户角色后重新登录。'],
        ['长时间使用后请求失效', '当前会话令牌可能已过期，保存当前工作并重新登录。'],
    ],
    [5.0, 10.0],
)
p('退出操作', 'Heading 3')
bullets([
    '结束实验或任务，确认控制页面已停止运行。',
    '点击界面右上角的退出或返回登录入口。',
    '在共享计算机上确认已回到登录页，不保留账号密码。',
])
p('提示：', None, bold_lead='提示：')
doc.paragraphs[-1].add_run('登录凭据只用于本人授权范围，不要在录屏、截图或日志中暴露密码和有效令牌。')
for run in doc.paragraphs[-1].runs:
    set_run_font(run)

page_break()
chapter('3 科研人员工作台')
p('3.1 EEG 实时监测', 'Heading 2')
p('进入科研工作台后，默认页显示多通道 EEG 波形、频谱或能力分布、无人机状态和控制台输出。开始实验前，应先确认数据源在线、波形持续刷新、各通道没有长时间饱和或平直。')
figure(ROOT / 'frames2/frame_01_001.07.png', '图 3-1 EEG 实时监测与无人机状态总览')
bullets([
    '观察波形是否连续、幅值是否明显异常。',
    '查看无人机卡片的在线状态，确认演示对象数量与预期一致。',
    '查看控制台输出，确认采集、解码或映射模块没有持续报错。',
])

page_break()
p('3.2 范式参数配置', 'Heading 2')
p('点击顶部“范式”进入范式配置页。界面支持选择 SSVEP、MI 或混合范式，并根据实验方案设置刺激频率、刺激尺寸和占空比等参数。')
figure(ROOT / 'frames2/frame_04_007.47.png', '图 3-2 SSVEP 范式参数配置')
bullets([
    '先选择范式类型，再选择对应任务或模式。',
    '按实验方案选择刺激频率，并调整尺寸和占空比。',
    '点击应用或确认按钮，观察底部提示，确认配置已生效。',
    '若参数恢复默认值，检查是否切换了范式、账号会话是否有效或服务是否已重启。',
])

page_break()
p('3.3 算法与控制配置', 'Heading 2')
p('算法页面用于选择解码模型并查看模型版本、训练集、准确率、延迟和适用范式。选择前应确认模型与当前实验范式相匹配。')
figure(ROOT / 'frames2/frame_06_011.73.png', '图 3-3 解码算法选择与模型信息', width=5.35)
p('控制页面用于调整控制器和相关参数。录屏中展示了 PID 参数配置和多种控制方法入口。调整参数后应先在仿真或安全演示环境中验证，不得直接用于未经验证的实机任务。')
figure(ROOT / 'frames2/frame_09_018.13.png', '图 3-4 控制器与 PID 参数配置', width=5.15)

page_break()
p('3.4 实验数据回看', 'Heading 2')
p('点击顶部“数据”进入实验数据工作台。左侧选择实验日期和记录，中间查看脑中活动或多通道曲线，顶部标签用于切换数据视图。')
figure(ROOT / 'frames2/frame_12_024.53.png', '图 3-5 实验记录与脑中活动回看')
bullets([
    '从左侧记录列表选择需要分析的实验。',
    '核对实验时间、编号或被试信息，避免打开错误记录。',
    '在不同数据视图之间切换，查看标记事件与曲线的时间对应关系。',
    '导出或共享数据前执行脱敏，避免包含未经授权的身份或生理信息。',
])

page_break()
chapter('4 作战操作员工作台')
p('4.1 蜂群态势查看', 'Heading 2')
p('态势页集中显示无人机位置、连接关系、目标区域、蜂群状态、目标状态、操作员状态和指令历史。操作前应先确认蜂群已连接且页面数据正在刷新。')
figure(ROOT / 'frames3/frame_03_003.90.png', '图 4-1 蜂群态势与目标状态总览')
bullets([
    '核对无人机编号、在线状态和高度等关键遥测。',
    '确认目标区域和目标状态与演示任务一致。',
    '查看指令历史，确认没有未处理的异常命令。',
])

page_break()
p('4.2 手动控制', 'Heading 2')
p('点击页面右上角的手动控制入口后，可通过上、下、左、右方向按钮发送演示指令。中央状态用于显示控制模式或脑控状态，右下方提供紧急停止。')
figure(ROOT / 'frames3/frame_08_011.71.png', '图 4-2 手动方向控制与操作反馈')
bullets([
    '确认页面顶部显示连接正常。',
    '一次只执行一个方向操作，等待底部反馈后再继续。',
    '观察置信度或识别结果，低于任务阈值时不要继续自动控制。',
    '发现状态不一致、控制失去响应或任务风险升高时，立即执行紧急停止。',
])

page_break()
p('4.3 脑状态与紧急停止', 'Heading 2')
p('脑状态页显示注意力、疲劳度、情绪压力等指标及趋势曲线，用于辅助判断当前操作状态。指标为辅助信息，不能替代操作员确认和安全程序。')
figure(ROOT / 'frames3/frame_06_008.59.png', '图 4-3 脑状态指标与趋势', width=5.3)
p('紧急停止', 'Heading 3')
figure(ROOT / 'frames3/frame_10_014.83.png', '图 4-4 紧急停止后的控制锁定状态', width=4.9)
p('按下“紧急停止”后，按钮状态变为“已急停”，页面显示控制已锁定。恢复前应查明原因、确认现场安全，并按组织规定完成复位；不得为继续演示而直接绕过锁定。')

page_break()
chapter('5 系统工程师工作台')
p('5.1 目标与设备状态', 'Heading 2')
p('系统工程师工作台左侧提供模块菜单，中央显示当前模块状态，右侧显示 INFO、WARN 和 ERROR 运行日志。先定位异常模块，再结合日志时间和状态变化判断原因。')
figure(ROOT / 'frames1/frame_01_000.55.png', '图 5-1 目标识别与跟踪状态', width=5.3)
figure(ROOT / 'frames1/frame_02_001.66.png', '图 5-2 设备状态监控', width=4.9)

page_break()
p('5.2 信号质量监控', 'Heading 2')
p('信号质量页显示 EEG、无人机遥测和惯性传感等数据源的频率、在线状态和质量，以及 CPU、内存、延迟和吞吐量趋势。')
figure(ROOT / 'frames1/frame_03_002.76.png', '图 5-3 信号质量与系统性能')
bullets([
    '频率异常：检查采集配置、数据源时钟和适配器状态。',
    '丢包或质量下降：检查链路负载、设备距离和网络干扰。',
    '延迟持续升高：结合 CPU、内存和吞吐量判断是否存在资源瓶颈。',
    '日志出现 WARN 或 ERROR：记录发生时间，并与曲线波动对照分析。',
])

page_break()
p('5.3 数据管理与深度诊断', 'Heading 2')
p('实验数据管理页用于查看实验记录、数据量和最近更新时间；深度诊断页用于查看系统自检结果及 CPU、GPU、内存等资源情况。')
figure(ROOT / 'frames1/frame_07_007.19.png', '图 5-4 实验数据管理', width=5.3)
figure(ROOT / 'frames1/frame_08_008.29.png', '图 5-5 系统深度诊断', width=4.9)
p('建议先执行系统检查，再查看异常模块和日志。演示数据只能证明界面流程可用，不能替代对真实采集卡、网络、飞控和传感器的独立诊断。')

page_break()
chapter('6 系统管理员工作台')
p('系统管理员工作台汇总设备、用户、告警、存储和备份状态。管理员应按最小权限原则维护账号，并定期检查存储空间和备份可用性。')
figure(ROOT / 'admin.png', '图 6-1 系统管理总览')
table(
    ['区域', '主要操作', '注意事项'],
    [
        ['设备管理', '查看设备类型、固件版本和在线状态。', '固件升级或设备下线前确认不会影响正在执行的实验。'],
        ['用户管理', '查看账号、角色和最近登录时间；按授权流程编辑角色。', '不得给普通账号授予不必要的管理员权限。'],
        ['存储管理', '查看使用率并清理缓存。', '清理前确认缓存不包含尚未归档的实验数据。'],
        ['系统备份', '执行立即备份或按流程恢复。', '恢复操作会影响当前数据，应先确认备份点和业务窗口。'],
    ],
    [2.6, 5.2, 7.2],
)

page_break()
chapter('7 常见问题与安全要求')
table(
    ['问题', '可能原因', '建议处理'],
    [
        ['无法登录', '认证服务未启动、网络不可达或凭据错误。', '检查服务、地址和端口；确认邮箱与密码；必要时联系管理员。'],
        ['EEG 波形不刷新', '数据源未连接、适配器未启用或采集状态异常。', '返回 EEG 页检查数据源与日志，重新连接后再开始实验。'],
        ['算法无法应用', '算法与范式不匹配或配置未保存。', '确认适用范式，重新选择模型并观察底部反馈。'],
        ['无人机状态离线', '演示网关未运行或真实链路中断。', '停止控制，检查网关、网络和设备电源；不要连续重复发送指令。'],
        ['页面出现 WARN 或 ERROR', '设备、数据源或系统资源异常。', '记录时间和模块，进入工程师工作台查看日志与深度诊断。'],
        ['急停后无法继续', '控制被安全锁定。', '查明原因并完成安全确认，再由授权人员执行复位。'],
    ],
    [3.2, 5.4, 6.4],
)
p('必须遵守的安全要求', 'Heading 2')
bullets([
    '仅在获得授权的科研、教学或演示环境中使用。',
    '真实飞行前必须建立独立的飞控限界、地理围栏、失联返航和物理急停措施。',
    '任何自动或脑控输出都应经过置信度阈值、权限校验和飞控侧安全仲裁。',
    '发生异常时优先保障人员与设备安全，保留日志并按组织流程报告。',
])

page_break()
chapter('附录 A 快速检查清单')
p('A.1 启动前', 'Heading 2')
bullets([
    '□ 后端服务和数据库已启动。',
    '□ 客户端能够访问认证服务。',
    '□ 使用正确角色的授权账号。',
    '□ EEG、演示网关或其他所需数据源已连接。',
    '□ 作战演示区域、目标和设备状态已核对。',
])
p('A.2 运行中', 'Heading 2')
bullets([
    '□ 波形、状态灯和时间戳持续更新。',
    '□ 算法、范式和控制参数与实验方案一致。',
    '□ 每次操作后均查看提示、日志或状态变化。',
    '□ 出现 WARN、ERROR、链路中断或低置信度时暂停操作。',
])
p('A.3 结束后', 'Heading 2')
bullets([
    '□ 任务或实验已停止，控制状态安全。',
    '□ 需要保留的数据已归档并完成脱敏。',
    '□ 异常日志、时间和操作步骤已记录。',
    '□ 已退出账号，共享计算机未保存凭据。',
])
p('文档结束', None, WD_ALIGN_PARAGRAPH.CENTER)

# Keep every run consistently black and in SimSun, including list labels and table text.
for paragraph in doc.paragraphs:
    for run in paragraph.runs:
        set_run_font(run)
for tbl in doc.tables:
    for row in tbl.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    if not run.bold:
                        set_run_font(run)

doc.core_properties.title = '脑控无人机蜂群作战系统 V1.0 操作手册'
doc.core_properties.subject = '系统操作说明'
doc.core_properties.author = '项目组'
doc.core_properties.comments = '根据现有手册、系统录屏及管理界面截图修订'
doc.save(OUT)
print(OUT)
