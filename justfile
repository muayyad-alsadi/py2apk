set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

# type-check the compiler package
check:
    ty check Py2APK

# lint the repository
lint:
    ruff check .

# build an sdist and wheel, then upload them to PyPI
publish:
    rm -rf dist build *.egg-info
    python3 -m build
    python3 -m twine check dist/*
    python3 -m twine upload dist/*
