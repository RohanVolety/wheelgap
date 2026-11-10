import json

import pytest

from wheelgap import pypi
from wheelgap.cli import main
from wheelgap.dockerfile import DockerfileError, ImageTarget, _heredocs, last_from, parse_from, read_dockerfile


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("python:3.13-alpine", ImageTarget("3.13", "musl-x86_64", musl="1.2")),
        ("--platform=linux/arm64 python:3.12-slim", ImageTarget("3.12", "linux-aarch64", glibc="2.41")),
        ('--platform="linux/arm64" python:3.12-slim', ImageTarget("3.12", "linux-aarch64", glibc="2.41")),
        ("--platform='linux/arm64' python:3.12-slim", ImageTarget("3.12", "linux-aarch64", glibc="2.41")),
        ('--platform="linux/arm/v7" python:3.13-alpine', ImageTarget("3.13", "musl-armv7l", musl="1.2")),
        ('--platform="" python:3.13', ImageTarget("3.13", "linux-x86_64", glibc="2.41")),
        ("python:3.14-slim-trixie", ImageTarget("3.14", "linux-x86_64", glibc="2.41")),
        ("python:3.13-bookworm", ImageTarget("3.13", "linux-x86_64", glibc="2.36")),
        ("python:3.11-slim-bullseye", ImageTarget("3.11", "linux-x86_64", glibc="2.31")),
        ("python:3.13.1-alpine3.22 AS runtime", ImageTarget("3.13", "musl-x86_64", musl="1.2")),
        ("docker.io/library/python:3.12", ImageTarget("3.12", "linux-x86_64", glibc="2.41")),
        ("--platform= python:3.13", ImageTarget("3.13", "linux-x86_64", glibc="2.41")),  # empty value: the default
        ("-- python:3.13", ImageTarget("3.13", "linux-x86_64", glibc="2.41")),  # BuildKit drops a bare --
        ("--platform=linux/arm64 -- python:3.13-alpine", ImageTarget("3.13", "musl-aarch64", musl="1.2")),
        ("python:3.13-slim@sha256:" + "0" * 64, ImageTarget("3.13", "linux-x86_64", glibc="2.41")),
        ("python:3.15.0a1-slim", ImageTarget("3.15", "linux-x86_64")),
        # No Debian release in the tag: the one Docker Hub publishes that Python on (see UNTAGGED_DEBIAN)
        ("python:3.13", ImageTarget("3.13", "linux-x86_64", glibc="2.41")),
        ("python:3.13-slim", ImageTarget("3.13", "linux-x86_64", glibc="2.41")),
        ("python:3.8-slim", ImageTarget("3.8", "linux-x86_64", glibc="2.36")),
        ("python:3.6", ImageTarget("3.6", "linux-x86_64", glibc="2.31")),
        ("python:3.16-slim", ImageTarget("3.16", "linux-x86_64")),  # not published yet: falls back to --glibc
        # A patch tag may be older than its X.Y tag (3.12.7-slim is still bookworm), so it falls back to --glibc too
        ("python:3.12.7-slim", ImageTarget("3.12", "linux-x86_64")),
        ("python:3.13.9", ImageTarget("3.13", "linux-x86_64")),
        ("python:3.13-windowsservercore-ltsc2022", ImageTarget("3.13", "win-amd64")),
        ("ghcr.io/astral-sh/uv:python3.13-bookworm-slim", ImageTarget("3.13", "linux-x86_64", glibc="2.36")),
        ("ghcr.io/astral-sh/uv:python3.14-trixie-slim", ImageTarget("3.14", "linux-x86_64", glibc="2.41")),
        ("ghcr.io/astral-sh/uv:0.13.7-python3.13-trixie-slim", ImageTarget("3.13", "linux-x86_64", glibc="2.41")),
        ("ghcr.io/astral-sh/uv:0.13-python3.13-trixie-slim", ImageTarget("3.13", "linux-x86_64", glibc="2.41")),
        ("--platform=linux/arm/v7 python:3.13-alpine", ImageTarget("3.13", "musl-armv7l", musl="1.2")),
        ("--platform=linux/ppc64le python:3.13-bookworm", ImageTarget("3.13", "linux-ppc64le", glibc="2.36")),
        ("--platform=linux/riscv64 python:3.14-trixie", ImageTarget("3.14", "linux-riscv64", glibc="2.41")),
        ("--platform=linux/s390x python:3.13-alpine", ImageTarget("3.13", "musl-s390x", musl="1.2")),
        ("--platform=linux/riscv64 python:3.13", ImageTarget("3.13", "linux-riscv64", glibc="2.41")),
        # Tags Docker Hub stopped rebuilding before it dropped s390x and ppc64le for bookworm and bullseye
        ("--platform=linux/s390x python:3.9-bookworm", ImageTarget("3.9", "linux-s390x", glibc="2.36")),
        ("--platform=linux/s390x python:3.12.7-bookworm", ImageTarget("3.12", "linux-s390x", glibc="2.36")),
        ("--platform=linux/ppc64le python:3.11.0-slim-bullseye", ImageTarget("3.11", "linux-ppc64le", glibc="2.31")),
        ("--platform=linux/s390x python:3.6", ImageTarget("3.6", "linux-s390x", glibc="2.31")),
        (
            "--platform=linux/arm64 ghcr.io/astral-sh/uv:python3.12-alpine3.23",
            ImageTarget("3.12", "musl-aarch64", musl="1.2"),
        ),
    ],
)
def test_parse_from(line, expected):
    assert parse_from(line) == expected


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("ubuntu:24.04", "only the official python images"),
        ("python", "must start with a Python version"),
        ("python:latest", "must start with a Python version"),
        ("ghcr.io/astral-sh/uv:bookworm-slim", r"must specify a Python version like python3\.13-alpine"),
        ("ghcr.io/astral-sh/uv:3.13-alpine", r"must specify a Python version like python3\.13-alpine"),
        ("ghcr.io/astral-sh/uv:0.13.7-alpine", r"must specify a Python version like python3\.13-alpine"),
        ("python:${PY}-slim", "build arguments"),
        ("--platform=$BUILDPLATFORM python:3.13", "build arguments"),
        ("--platform=linux/386 python:3.13", "only linux/amd64"),
        ("--platform=linux/s390x ghcr.io/astral-sh/uv:python3.13-bookworm-slim", "only published for linux/amd64"),
        ("python:3.13-slim-buster", "unknown image variant 'buster'"),
        ("--platform=linux/riscv64 python:3.13-bookworm", r"python:3\.13-bookworm isn't published for linux/riscv64"),
        (
            "--platform=linux/riscv64 python:3.13-slim-bookworm",
            r"isn't published for linux/riscv64 \(3\.13-trixie is\)",
        ),
        ("--platform=linux/s390x python:3.13-bookworm", "isn't published for linux/s390x"),
        ("--platform=linux/s390x python:3.13-slim-bookworm", "isn't published for linux/s390x"),
        ("--platform=linux/ppc64le python:3.13-bullseye", "isn't published for linux/ppc64le"),
        ("--platform=linux/s390x python:3.8-bullseye", r"isn't published for linux/s390x \(3\.8-bookworm is\)$"),
        ("--platform=linux/riscv64 python:3.9-bookworm", r"isn't published for linux/riscv64 \(3\.9-trixie is\)$"),
        ("--platform=linux/riscv64 python:3.8-bookworm", "isn't published for linux/riscv64$"),
        (
            "--platform=linux/riscv64 python:3.12.7-slim-bookworm",
            r"isn't published for linux/riscv64 \(3\.12-trixie is\)$",
        ),
    ],
)
def test_parse_from_errors(line, message):
    with pytest.raises(DockerfileError, match=message):
        parse_from(line)


def test_last_from_takes_the_final_stage():
    text = (
        "# syntax=docker/dockerfile:1\n"
        "FROM python:3.12-bookworm AS build\n"
        "RUN pip wheel . \\\n"
        "    -w /wheels\n"
        "from --platform=linux/arm64 \\\n"
        "    python:3.13-alpine\n"
        "COPY --from=build /wheels /wheels\n"
    )
    assert last_from(text) == "--platform=linux/arm64     python:3.13-alpine"  # joined as Docker joins
    assert parse_from(last_from(text)) == ImageTarget("3.13", "musl-aarch64", musl="1.2")


def test_no_from(tmp_path):
    path = tmp_path / "Dockerfile"
    path.write_text("RUN echo hi\n")
    with pytest.raises(DockerfileError, match="no FROM line"):
        read_dockerfile(path)


@pytest.mark.parametrize("image", ["python:3.13-alpine", "ghcr.io/astral-sh/uv:python3.13-alpine"])
def test_cli_dockerfile(capsys, fixtures, tmp_path, image):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(f"FROM {image}\n")
    code = main([str(fixtures / "uvproj"), "--dockerfile", str(dockerfile), "--json"])
    captured = capsys.readouterr()
    assert code in (0, 1), captured.err
    data = json.loads(captured.out)
    assert [(t["python"], t["platform"]) for t in data["targets"]] == [("3.13", "musl-x86_64")]


def test_cli_flags_beat_dockerfile(capsys, fixtures, tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.13-alpine\n")
    main([str(fixtures / "uvproj"), "--dockerfile", str(dockerfile), "-t", "win-amd64", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert [(t["python"], t["platform"]) for t in data["targets"]] == [("3.13", "win-amd64")]


@pytest.mark.parametrize(("image", "status"), [("python:3.13-slim", "wheel"), ("python:3.13-slim-bookworm", "source")])
def test_cli_untagged_image_uses_its_debian_glibc(capsys, monkeypatch, tmp_path, image, status):
    files = [{"filename": "trixielib-1.0-cp313-cp313-manylinux_2_39_x86_64.whl"}, {"filename": "trixielib-1.0.tar.gz"}]
    monkeypatch.setattr(pypi, "fetch_json", lambda url, timeout=20.0: {"info": {"version": "1.0"}, "urls": files})
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(f"FROM {image}\n")
    main(["trixielib==1.0", "--dockerfile", str(dockerfile), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["packages"][0]["results"] == {"linux-x86_64": status}


def test_cli_bad_dockerfile_exit_2(capsys, fixtures, tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM node:22\n")
    assert main([str(fixtures / "uvproj"), "--dockerfile", str(dockerfile)]) == 2
    assert "only the official python images" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("line", "musl"),
    [
        ("python:3.9-alpine3.12", "1.1"),
        ("python:3.9-alpine3.10", "1.1"),
        ("python:3.13-alpine3.13", "1.2"),
        ("python:3.13-alpine", "1.2"),
    ],
)
def test_alpine_musl_version(line, musl):
    assert parse_from(line).musl == musl  # Alpine 3.12 and earlier ship musl 1.1


@pytest.mark.parametrize("platform", ["linux/arm64", "linux/arm64,linux/amd64"])
def test_cli_docker_platform_names_keep_the_images_libc(capsys, fixtures, tmp_path, platform):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.13-alpine\n")
    main([str(fixtures / "uvproj"), "--dockerfile", str(dockerfile), "-t", platform, "--json"])
    platforms = [t["platform"] for t in json.loads(capsys.readouterr().out)["targets"]]
    assert all(p.startswith("musl-") for p in platforms)  # used to be linux-aarch64 (glibc)


def test_cli_wheelgap_names_still_win_over_the_image(capsys, fixtures, tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python:3.13-alpine\n")
    main([str(fixtures / "uvproj"), "--dockerfile", str(dockerfile), "-t", "linux-aarch64", "--json"])
    assert [t["platform"] for t in json.loads(capsys.readouterr().out)["targets"]] == ["linux-aarch64"]


def _dockerfile(tmp_path, text):
    path = tmp_path / "Dockerfile"
    path.write_text(text, encoding="utf-8")  # Docker reads UTF-8; Windows would otherwise write cp1252
    return path


def test_named_stage_is_followed(tmp_path):
    text = "FROM python:3.13-alpine AS base\nRUN apk add --no-cache libpq\n\nFROM base AS runtime\nCOPY . /app\n"
    assert read_dockerfile(_dockerfile(tmp_path, text)) == ImageTarget("3.13", "musl-x86_64", musl="1.2")


def test_chain_of_stages_and_platforms(tmp_path):
    text = (
        "FROM --platform=linux/arm64 python:3.12-slim-bookworm AS base\n"
        "FROM base AS deps\n"
        "FROM python:3.11 AS unrelated\n"
        "FROM deps\n"
    )
    assert read_dockerfile(_dockerfile(tmp_path, text)) == ImageTarget("3.12", "linux-aarch64", glibc="2.36")
    # As in Docker, a stage keeps the platform of the stage it builds on; --platform here changes nothing.
    text += "FROM --platform=linux/amd64 deps\n"
    assert read_dockerfile(_dockerfile(tmp_path, text)) == ImageTarget("3.12", "linux-aarch64", glibc="2.36")
    text = "FROM python:3.13-slim AS base\nFROM --platform=linux/arm64 base\n"
    assert read_dockerfile(_dockerfile(tmp_path, text)) == ImageTarget("3.13", "linux-x86_64", glibc="2.41")


def test_stage_names_are_lowercased_like_docker(tmp_path):
    # Docker lowercases the AS name but looks a reference up as written (and image names are lowercase).
    text = "FROM python:3.13 as Base\nFROM base\n"
    assert read_dockerfile(_dockerfile(tmp_path, text)) == ImageTarget("3.13", "linux-x86_64", glibc="2.41")
    text = "FROM python:3.13 AS build-env_1.2\nFROM build-env_1.2\n"
    assert read_dockerfile(_dockerfile(tmp_path, text)) == ImageTarget("3.13", "linux-x86_64", glibc="2.41")
    with pytest.raises(DockerfileError, match="FROM BASE: not an earlier stage"):
        read_dockerfile(_dockerfile(tmp_path, "FROM python:3.13 as Base\nFROM BASE\n"))


def test_unknown_stage_is_a_clear_error(tmp_path):
    text = "FROM python:3.13-alpine AS base\nFROM bse\n"
    with pytest.raises(DockerfileError, match=r"FROM bse: not an earlier stage \(base\) or an official python image"):
        read_dockerfile(_dockerfile(tmp_path, text))


def test_scratch_after_stages_keeps_the_usual_error(tmp_path):
    text = "FROM python:3.13-alpine AS build\nRUN pip wheel . -w /w\nFROM scratch\nCOPY --from=build /w /w\n"
    with pytest.raises(DockerfileError, match="scratch: only the official python images"):
        read_dockerfile(_dockerfile(tmp_path, text))


_HEREDOC_LIKE_LINES = [
    "RUN echo $((1<<SHIFT))",
    'RUN python -c "print(1<<n)"',
    "CMD cat <<EOF",
    "ENV X=<<EOF",
    'LABEL note="a<<b"',
    'RUN ["sh", "-c", "cat <<EOF"]',
    "RUN cat <<<word",
]


@pytest.mark.parametrize("line", _HEREDOC_LIKE_LINES)
def test_lines_with_heredoc_like_text_never_hide_the_final_from(tmp_path, line):
    # wheelgap doesn't parse heredocs, so whatever these mean to Docker, the final FROM is still found.
    text = f"FROM python:3.13-alpine AS base\n{line}\nFROM python:3.12-slim\n"
    assert read_dockerfile(_dockerfile(tmp_path, text)) == ImageTarget("3.12", "linux-x86_64", glibc="2.41")


def _docs(*names):
    return [(name, False) for name in names]


# BuildKit's TestHeredocsFromLine (frontend/dockerfile/parser/parser_heredoc_test.go at cedaeca), less a repeated
# row. Its loop only checks the heredocs found, so its `RUN <<- EOF` row (expecting EOF) passes with none, and none
# is right: the lexer splits `<<-` from `EOF`.
_BUILDKIT_HEREDOCS_FROM_LINE = [
    ("RUN <<EOF", _docs("EOF")),
    ("RUN <<-EOF", [("EOF", True)]),
    ("RUN <<- EOF", []),
    ("RUN << -EOF", _docs("-EOF")),
    ("RUN <<'EOF'", _docs("EOF")),
    ("RUN 4<<EOF", _docs("EOF")),
    ("RUN <<EOF <<EOF2", _docs("EOF", "EOF2")),
    ("RUN '<<EOF'", []),
    ('RUN "<<EOF"', []),
]


@pytest.mark.parametrize(
    ("instruction", "expected"),
    [
        *_BUILDKIT_HEREDOCS_FROM_LINE,
        ('RUN <<"EOF"', _docs("EOF")),
        (r"RUN <<\EOF", _docs("EOF")),
        ('RUN <<E"O"F', _docs("EOF")),
        ("RUN 2<<EOF", _docs("EOF")),
        ("COPY <<FILE1 <<FILE2 /dest", _docs("FILE1", "FILE2")),
        ("run <<EOF", _docs("EOF")),
        ("RUN << EOF", _docs("EOF")),
        ("RUN <<\tEOF", _docs("EOF")),
        ("RUN --mount=type=cache,target=/x <<EOF", _docs("EOF")),
        ("RUN --mount=type=secret,id=a -- <<EOF", _docs("EOF")),
        # From BuildKit's TestParseExtractsHeredoc.
        ("USER <<INVALID", []),
        ("RUN <<COMPLEX python3", _docs("COMPLEX")),
        ("COPY <<file.txt /dest", _docs("file.txt")),
        ("RUN <<eo'f'", _docs("eof")),
        (r"RUN <<eo\'f", _docs("eo'f")),
        (r"RUN <<'e'o\'f", _docs("eo'f")),
        ("RUN <<'one two'", _docs("one two")),
        ("RUN <<$EOF", _docs("$EOF")),
        ("RUN <<  EOF  > foo", _docs("EOF")),
        # `$(` and `$((` are ordinary text to BuildKit.
        ("RUN echo $((1 << BITS))", _docs("BITS))")),
        ("RUN x=$(cat <<EOF)", _docs("EOF)")),
        # ${x:-y} (any ${} with a modifier) empties the word it's in, as BuildKit's shared word buffer does; $$ is a
        # whole parameter, so the { after it is plain text.
        ("RUN cat <<EOF${x:-y}", []),
        ("RUN cat <<EOF$${x:-y}", _docs("EOF$${x:-y}")),
        ("RUN cat <<EOF${x}", _docs("EOF${x}")),
        ("RUN cat <<EOF$x", _docs("EOF$x")),
        ("RUN echo ${x:- <<EOF}", _docs("EOF}")),
        # Inside double quotes \ escapes only " $ and \, and is kept otherwise; a trailing \ is dropped.
        ('RUN <<"E\\OF"', _docs("E\\OF")),
        ('RUN <<"E\\"OF"', _docs('E"OF')),
        ("RUN <<EOF\\", _docs("EOF")),
        ("RUN <<EOF\\ ", _docs("EOF ")),
        # Exec form is only a JSON array of strings.
        ("RUN [ -f /x ] && cat <<EOF > /y", _docs("EOF")),
        ("RUN [NaN] <<EOF", _docs("EOF")),
        ('RUN --mount=type=cache,target=/x ["cat", "<<EOF"]', []),
        ('RUN -- ["<<EOF"]', []),
        ('RUN --x=` <<EOF ["a"]', _docs("EOF")),  # ` is plain text in a flag, unless it's the escape char
        ('COPY ["<<EOF", "/x"]', []),
        # BuildKit reads the --flags byte by byte: U+2003 isn't a space there, so the flag runs on to `["a"]`.
        ('RUN --x\u2003<<EOF ["a"]', []),
        # A lexer error (an unterminated quote, a bad ${}) is ignored, leaving no words.
        ('RUN cat <<EOF "unterminated', []),
        ("RUN cat <<'EOF", []),
        ("RUN cat <<EOF ${x", []),
        # Words split on Go's unicode.IsSpace (not \x1c-\x1f, unlike str.isspace); `<<` takes only tabs, \r and
        # spaces after it; the keyword splits off on ASCII whitespace only.
        ("RUN <<\x1cEOF", _docs("\x1cEOF")),
        ("RUN <<EOF\u00a0x", _docs("EOF")),
        ("RUN <<\vEOF", []),
        ("RUN\v<<EOF", _docs("EOF")),
        ("RUN\u00a0<<EOF", []),
        ("RUN <<\ufeffEOF", _docs("EOF")),  # Go's text/scanner skips a leading byte order mark
        # An empty delimiter is no heredoc.
        ('RUN <<""', []),
        ("RUN <<''", []),
        ("RUN <<''EOF", _docs("EOF")),
        ("RUN <<", []),
        # ONBUILD of RUN, COPY or ADD only, with keywords lowercased as Go does: U+0130 lowers to i, U+0131 doesn't.
        ("ONBUILD RUN <<EOF", _docs("EOF")),
        ("onbuild copy <<EOF /x", _docs("EOF")),
        ("ONBUILD CMD <<EOF", []),
        ("ONBUILD ONBUILD RUN <<EOF", []),
        ('ONBUILD RUN ["<<EOF"]', []),
        ("ONBUILD <<EOF", []),
        ("ONBU\u0130LD RUN <<EOF", _docs("EOF")),
        ("onbu\u0131ld run <<EOF", []),
        *[(line, []) for line in _HEREDOC_LIKE_LINES],
    ],
)
def test_heredocs(instruction, expected):
    assert _heredocs(instruction) == expected


@pytest.mark.parametrize(
    ("instruction", "expected"),
    [
        ("RUN <<`EOF`", _docs("`EOF`")),
        (r"RUN <<\EOF", _docs("EOF")),
        ('RUN --x=` <<EOF ["a"]', []),  # the escaped space keeps `<<EOF` in the flag, so this is exec form
    ],
)
def test_heredocs_lex_with_backslash_under_a_backtick_escape(instruction, expected):
    # BuildKit's heredoc lexer always escapes with \; the `# escape=` char only matters to the --flags.
    assert _heredocs(instruction, "`") == expected


@pytest.mark.parametrize(
    ("instruction", "error"),
    [
        ("RUN echo ${x:- <<'E F'}", r"can't read the heredoc <<'E \(unexpected end of statement"),
        ('RUN ["cat <<EOF", 1]', "JSON array that isn't all strings"),
        ('ONBUILD RUN ["cat <<EOF", 1]', "JSON array that isn't all strings"),
    ],
)
def test_heredocs_raise_where_buildkit_rejects_the_line(instruction, error):
    with pytest.raises(DockerfileError, match=error):
        _heredocs(instruction)


def test_unknown_stage_lists_only_earlier_stages(tmp_path):
    text = "FROM python:3.13-alpine AS base\nFROM bse AS final\n"
    with pytest.raises(DockerfileError, match=r"not an earlier stage \(base\) or"):
        read_dockerfile(_dockerfile(tmp_path, text))
    # A stage built on a non-python image gets the usual image error, not a stage one.
    with pytest.raises(DockerfileError, match="alpine: only the official python images"):
        read_dockerfile(_dockerfile(tmp_path, "FROM alpine AS base\nFROM base\n"))


def test_python_lines_starting_with_from_are_not_errors(tmp_path):
    text = "FROM python:3.13 AS a\nRUN python - <<EOF\nfromtimestamp = 1\nEOF\nFROM python:3.12\n"
    assert read_dockerfile(_dockerfile(tmp_path, text)) == ImageTarget("3.12", "linux-x86_64", glibc="2.41")


def test_python_imports_in_heredocs_are_not_from_lines(tmp_path):
    text = (
        "FROM python:3.12-slim AS app\n"
        "COPY . /app\n"
        "FROM python:3.13-alpine\n"
        "COPY --from=app /app /app\n"
        "RUN python - <<EOF\n"
        "from app import main\n"
        "EOF\n"
    )
    assert read_dockerfile(_dockerfile(tmp_path, text)) == ImageTarget("3.13", "musl-x86_64", musl="1.2")


@pytest.mark.parametrize(
    "text",
    [
        # A blank line inside a continued instruction is skipped, so FROM base is part of the RUN.
        "FROM python:3.12-slim AS base\nFROM python:3.13-alpine\nRUN rm -rf /tmp/* \\\n\nFROM base\n",
        # The same with a comment line in between.
        "FROM python:3.12-slim AS base\nFROM python:3.13-alpine\nRUN rm -rf /tmp/* \\\n# note\nFROM base\n",
        # With `# escape=` + "`", a trailing backtick continues the line (and a backslash doesn't).
        "# escape=`\nFROM python:3.12-windowsservercore AS base\nFROM python:3.13-alpine\nRUN echo `\nFROM base\n",
    ],
)
def test_continuations_like_docker(tmp_path, text):
    assert read_dockerfile(_dockerfile(tmp_path, text)) == ImageTarget("3.13", "musl-x86_64", musl="1.2")


@pytest.mark.parametrize(
    "text",
    [
        # A comment ending in a backslash doesn't continue onto the next line.
        "FROM python:3.13-alpine AS app\n# path C:\\\nFROM python:3.12-slim\n",
        # A doubled backslash is an escaped backslash, not a continuation.
        "FROM python:3.13-alpine AS app\nRUN echo \\\\\nFROM python:3.12-slim\n",
        # Under `# escape=` + "`", a trailing backslash is just a character.
        "# escape=`\nFROM python:3.13-alpine AS app\nRUN dir C:\\\nFROM python:3.12-slim\n",
    ],
)
def test_lines_that_do_not_continue(tmp_path, text):
    assert read_dockerfile(_dockerfile(tmp_path, text)) == ImageTarget("3.12", "linux-x86_64", glibc="2.41")


@pytest.mark.parametrize(
    "text",
    [
        "FROM python:3.12-alpine AS build\nFROM --platform=linux/arm64 \\\n    python:3.11-slim \\\n# end\n",
        "FROM python:3.12-alpine AS build\nFROM --platform=linux/arm64 python:3.11-slim \\",
        "\ufeffFROM --platform=linux/arm64 python:3.11-slim\n",  # a UTF-8 BOM, which Docker strips
        "FROM --platform=linux/arm64 python:3.1\\\n1-slim\n",  # no space is added at the join
    ],
)
def test_final_line_edge_cases(tmp_path, text):
    path = tmp_path / "Dockerfile"
    path.write_text(text, encoding="utf-8")
    assert read_dockerfile(path) == ImageTarget("3.11", "linux-aarch64", glibc="2.41")


def test_escape_directive_rules(tmp_path):
    # An unknown directive-looking comment ends the directives, so the later escape= is ignored.
    text = "# version=2\n# escape=`\nFROM python:3.12-alpine\nRUN echo \\\nFROM python:3.11-slim\n"
    assert read_dockerfile(_dockerfile(tmp_path, text)) == ImageTarget("3.12", "musl-x86_64", musl="1.2")
    with pytest.raises(DockerfileError, match="invalid escape directive"):
        read_dockerfile(_dockerfile(tmp_path, "# escape=x\nFROM python:3.12\n"))


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("# escape=`\n# escape=\\\nFROM python:3.12\n", "only one escape parser directive"),
        ("# syntax=docker/dockerfile:1\n# syntax=docker/dockerfile:1\nFROM python:3.12\n", "only one syntax"),
        ("FROM python:3.12 AS a\nFROM\\\npython:3.11\n", "unknown instruction 'FROMpython:3.11'"),
        ("FROM python:3.12 AS a\nFROM\n", "FROM with no image"),
        ("FROM --platform linux/arm64 python:3.13\n", "--platform needs a value"),
        ("FROM --foo=bar python:3.13\n", "unknown flag '--foo=bar'"),
        ("FROM python:3.13-alpine AS\n", "expected 'IMAGE' or 'IMAGE AS NAME'"),
        ("FROM python:3.13 AS\n", "expected 'IMAGE' or 'IMAGE AS NAME'"),
        ("FROM python:3.13 builder\n", "expected 'IMAGE' or 'IMAGE AS NAME'"),
        ("FROM python:3.13 AS a b\n", "expected 'IMAGE' or 'IMAGE AS NAME'"),
        ("FROM --platform=linux/arm64 --platform=linux/amd64 python:3.13\n", "duplicate --platform flag"),
        ("FROM --platform= --platform=linux/arm64 python:3.13\n", "duplicate --platform flag"),
        ('FROM --platform="linux/arm64 python:3.13\n', "unterminated quote"),
        ("FROM --platform='linux/arm64 python:3.13\n", "unterminated quote"),
        ('FROM --platform="linux\\/arm64" python:3.13\n', "escapes in --platform aren't supported"),
        ("FROM python:3.13 AS\nFROM python:3.12\n", "expected 'IMAGE' or 'IMAGE AS NAME'"),
        ("FROM python:3.13 AS 1build\n", "invalid stage name '1build'"),
        ("FROM python:3.13 AS my@stage\n", "invalid stage name 'my@stage'"),
        ("FROM python:3.13 AS 1build\nFROM python:3.12\n", "invalid stage name '1build'"),  # any stage, as in Docker
        ("FROM --platform=linux/arm64\u00a0python:3.13\n", "no image"),
        ("FROM python:3.13 AS\u00a0stage\n", "expected 'IMAGE' or 'IMAGE AS NAME'"),
        ("FROM\u00a0python:3.13\n", "unknown instruction"),  # not a FROM to Docker at all
        ("FROM\u00a0--platform=linux/arm64 python:3.13\n", "unknown instruction"),
        ("FROM python:3.12\nFROM\u00a0python:3.13\n", "unknown instruction"),  # mustn't fall back to 3.12
        ("FROM python:3.12\x85FROM python:3.13\n", "expected 'IMAGE' or 'IMAGE AS NAME'"),
        ("FROM python:3.12\rFROM python:3.13", "expected 'IMAGE' or 'IMAGE AS NAME'"),
        ("FROM PYTHON:3.13\n", "repository name must be lowercase"),  # Docker rejects it, doesn't lowercase it
        ("FROM docker.io/library/PYTHON:3.13\n", "repository name must be lowercase"),
        ("FROM Docker.IO/library/python:3.12\n", "only the official python images"),  # host kept as written
        ("FROM python:3.13-SLIM\n", "unknown image variant 'SLIM'"),  # Docker Hub tags are case-sensitive
        ("FROM python:3.13-alpine3.1\u0662\n", "invalid tag"),
        ("FROM ghcr.io/astral-sh/uv:0.\u0665-python3.13-alpine\n", "invalid tag"),
        ("FROM python:3.1٣\n", "invalid tag"),  # an Arabic-Indic digit isn't allowed in a Docker tag
    ],
)
def test_dockerfiles_docker_rejects(tmp_path, text, message):
    with pytest.raises(DockerfileError, match=message):
        read_dockerfile(_dockerfile(tmp_path, text))


@pytest.mark.parametrize("space", ["\v", "\f", "\r"])
def test_ascii_whitespace_inside_a_line_separates_words(tmp_path, space):
    # \v, \f and \r don't end a line for Docker (only \n does), but they do separate words, as spaces do.
    path = tmp_path / "Dockerfile"
    path.write_bytes(f"FROM python:3.13{space}AS build\nFROM build\n".encode())
    assert read_dockerfile(path) == ImageTarget("3.13", "linux-x86_64", glibc="2.41")


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        # \r\r\n (CRLF written in text mode on Windows): every trailing \r goes, so the continuation holds.
        (
            b"FROM python:3.13-alpine\nRUN echo hi \\\r\r\nFROM python:3.12-slim\n",
            ImageTarget("3.13", "musl-x86_64", musl="1.2"),
        ),
        (
            b"FROM python:3.12 AS a\r\nFROM --platform=linux/arm64 \\\r\n python:3.11-slim\r\n",
            ImageTarget("3.11", "linux-aarch64", glibc="2.41"),
        ),
    ],
)
def test_crlf_continuations(tmp_path, data, expected):
    path = tmp_path / "Dockerfile"
    path.write_bytes(data)
    assert read_dockerfile(path) == expected


def test_crlf_dockerfile_parses(tmp_path):
    path = tmp_path / "Dockerfile"
    path.write_bytes(b"FROM python:3.13-alpine\r\nRUN echo hello\r\n")
    assert read_dockerfile(path) == ImageTarget("3.13", "musl-x86_64", musl="1.2")


def test_dockerfile_that_is_not_utf8_is_a_clear_error(tmp_path):
    path = tmp_path / "Dockerfile"
    path.write_bytes(b"FROM --platform=linux/arm64\xa0python:3.13\n")  # cp1252: Docker would read 0xa0 as a space
    with pytest.raises(DockerfileError, match="not UTF-8 \\(byte 0xa0 at offset 27\\), pass -p/-t instead"):
        read_dockerfile(path)
