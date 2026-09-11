def test_bullet_html_escapes_raw_markup():
    from src.review_format import render_points
    result = render_points('- **핵심**: 지원 확대\n- <script>alert(1)</script>')
    assert '<strong>핵심</strong>' in result
    assert result.count('<li>') == 2
    assert '<script>' not in result


def test_editorial_report_has_hierarchy_and_title_links():
    from src.review_format import editorial_html, editorial_md
    data = {'summary': '- **정책**: 지원 확대', 'sections': [
        {'title': '서비스 연계', 'body': '- **확인**: 데이터 출처', 'source_ids': [1]}],
        'sources': [{'id': 1, 'title': '근거 기사', 'url': 'https://example.com/' + 'x'*300},
                    {'id': 2, 'title': 'unsafe', 'url': 'javascript:alert(1)'}]}
    result = editorial_html(data)
    assert '<h3>서비스 연계</h3>' in result
    assert 'class="brief-sources"' in result
    assert '>근거 기사</a>' in result
    assert 'javascript:' not in result
    assert '### 서비스 연계' in editorial_md(data)
    assert '[근거 기사](https://' in editorial_md(data)
