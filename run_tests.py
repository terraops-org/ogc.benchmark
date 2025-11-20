#!/usr/bin/env python3
"""Test runner script for OGC benchmark suite using Click"""
import sys
import subprocess
import click


def run_command(cmd, description):
    """Run a command and return the result"""
    click.echo(f"\n{'='*60}")
    click.echo(f"Running: {description}")
    click.echo(f"Command: {' '.join(cmd)}")
    click.echo('='*60)

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        click.echo(result.stdout)
        if result.stderr:
            click.echo(click.style("STDERR:", fg='red') + f" {result.stderr}")
        return result.returncode == 0
    except Exception as e:
        click.echo(click.style(f"Error running command: {e}", fg='red'))
        return False


@click.command()
@click.option('--unit', is_flag=True, help='Run unit tests only')
@click.option('--integration', is_flag=True, help='Run integration tests only')
@click.option('--live', is_flag=True, help='Run live PDOK service tests (requires network)')
@click.option('--coverage', is_flag=True, help='Generate coverage report')
@click.option('--verbose', '-v', is_flag=True, help='Verbose output')
@click.option('--fast', is_flag=True, help='Skip slow and network tests')
@click.option('--pdok-only', is_flag=True, help='Run only PDOK service validation tests')
def main(unit, integration, live, coverage, verbose, fast, pdok_only):
    """Run OGC benchmark tests with various options."""

    # Base pytest command
    pytest_cmd = ['python', '-m', 'pytest']

    if verbose:
        pytest_cmd.append('-v')

    if coverage:
        pytest_cmd.extend(['--cov=.', '--cov-report=term-missing', '--cov-report=html'])

    # Test selection logic
    test_markers = []

    if unit:
        test_markers.append('unit')
        click.echo(click.style("🧪 Running unit tests only", fg='blue'))
    elif integration:
        test_markers.append('integration')
        click.echo(click.style("🔧 Running integration tests only", fg='blue'))
    elif live:
        test_markers.append('network')
        click.echo(click.style("🌐 Running live PDOK service tests (requires network)", fg='blue'))
    elif fast:
        test_markers.append('not slow and not network')
        click.echo(click.style("⚡ Running fast tests only (no network/slow tests)", fg='blue'))
    elif pdok_only:
        pytest_cmd.extend(['tests/test_live_services.py::TestPDOKServiceValidation'])
        click.echo(click.style("🇳🇱 Running PDOK service validation only", fg='blue'))
    else:
        click.echo(click.style("🚀 Running all tests", fg='green'))

    if test_markers:
        pytest_cmd.extend(['-m', ' and '.join(test_markers)])

    # Run syntax check first
    click.echo(click.style("Checking Python syntax...", fg='yellow'))
    syntax_check = run_command([
        'python', '-m', 'py_compile',
        'wmts.py', 'wms.py', 'wfs.py', 'exceptions.py'
    ], "Python syntax check")

    if not syntax_check:
        click.echo(click.style("❌ Syntax check failed!", fg='red'))
        return 1

    click.echo(click.style("✅ Syntax check passed!", fg='green'))

    # Run the tests
    success = run_command(pytest_cmd, "pytest test suite")

    if success:
        click.echo(click.style("\n🎉 All tests passed!", fg='green', bold=True))
        return 0
    else:
        click.echo(click.style("\n❌ Some tests failed!", fg='red', bold=True))
        return 1


@click.group()
def cli():
    """OGC Benchmark Test Suite"""
    pass


@cli.command()
@click.option('--service', type=click.Choice(['wms', 'wfs', 'wmts', 'all']), default='all',
              help='Which PDOK service to test')
def pdok_check(service):
    """Quick check of PDOK service availability"""
    import requests

    services = {
        'wms': "https://service.pdok.nl/kadaster/plu/wms/v1_0?request=getcapabilities&service=wms",
        'wfs': "https://service.pdok.nl/lv/bag/wfs/v2_0?request=getCapabilities&service=WFS",
        'wmts': "https://service.pdok.nl/lv/bgt/wmts/v1_0?request=GetCapabilities&service=WMTS"
    }

    if service == 'all':
        test_services = services
    else:
        test_services = {service: services[service]}

    click.echo(click.style("🇳🇱 Checking PDOK service availability...", fg='blue', bold=True))

    all_ok = True
    for service_name, url in test_services.items():
        try:
            with click.progressbar([1], label=f'Testing {service_name.upper()}') as bar:
                response = requests.get(url, timeout=10)
                bar.update(1)

            if response.status_code == 200:
                click.echo(click.style(f"✅ {service_name.upper()} service is accessible", fg='green'))
            else:
                click.echo(click.style(f"❌ {service_name.upper()} returned {response.status_code}", fg='red'))
                all_ok = False
        except Exception as e:
            click.echo(click.style(f"❌ {service_name.upper()} failed: {e}", fg='red'))
            all_ok = False

    if all_ok:
        click.echo(click.style("\n🎉 All PDOK services are accessible!", fg='green', bold=True))
    else:
        click.echo(click.style("\n⚠️  Some PDOK services have issues", fg='yellow', bold=True))


@cli.command()
def install_deps():
    """Install test dependencies"""
    click.echo(click.style("📦 Installing test dependencies...", fg='blue'))
    success = run_command([
        'pip', 'install', '-r', 'requirements-dev.txt'
    ], "Installing development dependencies")

    if success:
        click.echo(click.style("✅ Dependencies installed successfully!", fg='green'))
    else:
        click.echo(click.style("❌ Failed to install dependencies", fg='red'))


@cli.command()
@click.option('--open', 'open_report', is_flag=True, help='Open coverage report in browser')
def coverage(open_report):
    """Generate test coverage report"""
    click.echo(click.style("📊 Generating coverage report...", fg='blue'))

    success = run_command([
        'python', '-m', 'pytest', '--cov=.', '--cov-report=html', '--cov-report=term'
    ], "Coverage analysis")

    if success:
        click.echo(click.style("✅ Coverage report generated!", fg='green'))
        click.echo("HTML report: htmlcov/index.html")

        if open_report:
            import webbrowser
            webbrowser.open('htmlcov/index.html')
    else:
        click.echo(click.style("❌ Coverage generation failed", fg='red'))


# Add the main command to the group
cli.add_command(main, name='test')

if __name__ == '__main__':
    cli()