#!/usr/bin/env python3
import argparse
import cProfile
import sys
import time

from dotenv import load_dotenv
from nodes.actions.action import ActionNode
from nodes.instance import InstanceLoader
from common.perf import PerfCounter
import rich.traceback
from rich.table import Table
from rich.console import Console
import pandas as pd


if True:
    # Print traceback for warnings
    import traceback
    import warnings

    def warn_with_traceback(message, category, filename, lineno, file=None, line=None):
        log = sys.stderr
        traceback.print_stack()
        log.write(warnings.formatwarning(message, category, filename, lineno, line))

    warnings.showwarning = warn_with_traceback
    # Pretty tracebacks
    rich.traceback.install()

load_dotenv()

django_initialized = False

pd.set_option('display.max_rows', 500)


def init_django():
    global django_initialized
    if django_initialized:
        return
    import os
    import django
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "paths.settings")
    django.setup()
    django_initialized = True


parser = argparse.ArgumentParser(description='Execute the computational graph')
parser.add_argument('--instance', type=str, help='instance identifier')
parser.add_argument('--config', type=str, help='config yaml file')
parser.add_argument('--baseline', action='store_true', help='generate baseline scenario values')
parser.add_argument('--scenario', type=str, help='select scenario')
parser.add_argument('--param', action='append', type=str, help='set a parameter')
parser.add_argument('--list-params', action='store_true', help='list parameters')
parser.add_argument('--debug-nodes', type=str, nargs='+', help='enable debug messages for nodes')
parser.add_argument('--check', action='store_true', help='perform sanity checking')
parser.add_argument('--skip-cache', action='store_true', help='skip caching')
parser.add_argument('--node', type=str, nargs='+', help='compute node')
parser.add_argument('--pull-datasets', action='store_true', help='refresh all datasets')
parser.add_argument('--print-graph', action='store_true', help='print the graph')
parser.add_argument('--update-instance', action='store_true', help='update an existing InstanceConfig instance')
parser.add_argument('--update-nodes', action='store_true', help='update existing NodeConfig instances')
parser.add_argument('--delete-stale-nodes', action='store_true', help='delete NodeConfig instances that no longer exist')
parser.add_argument('--print-action-efficiencies', action='store_true', help='calculate and print action efficiencies')
parser.add_argument('--show-perf', action='store_true', help='show performance info')
parser.add_argument('--profile', action='store_true', help='profile computation performance')
# parser.add_argument('--sync', action='store_true', help='sync db to node contents')
args = parser.parse_args()

if (args.instance and args.config) or (not args.instance and not args.config):
    print('Specify either "--instance" or "--config"')
    exit(1)

if args.instance:
    init_django()
    from nodes.models import InstanceConfig
    instance_obj: InstanceConfig = InstanceConfig.objects.get(identifier=args.instance)
    instance = instance_obj.get_instance()
    context = instance.context
else:
    loader = InstanceLoader.from_yaml(args.config)
    context = loader.context
    instance = loader.instance

if args.pull_datasets:
    context.pull_datasets()

if args.check:
    context.check_mode = True

if args.show_perf:
    from common.perf import PerfCounter

    PerfCounter.change_level(PerfCounter.Level.DEBUG)
    context.perf_context.start()


profile: cProfile.Profile | None
if args.profile:
    profile = cProfile.Profile()
else:
    profile = None


def print_metric(metric):
    print(metric)

    print('Historical:')
    vals = metric.get_historical_values(context)
    for val in vals:
        print(val)

    print('\nBaseline forecast:')
    vals = metric.get_baseline_forecast_values(context)
    for val in vals:
        print(val)

    print('\nRoadmap scenario:')
    vals = metric.get_forecast_values(context)
    for val in vals:
        print(val)


if args.print_graph:
    context.print_graph()

if args.skip_cache:
    context.skip_cache = True

if args.scenario:
    context.activate_scenario(context.get_scenario(args.scenario))

if args.list_params:
    context.print_all_parameters()

for node_id in (args.debug_nodes or []):
    node = context.get_node(node_id)
    node.debug = True

if args.baseline:
    pc = PerfCounter('Baseline')
    if profile is not None:
        profile.enable()
    pc.display('generating baseline values')
    context.cache.start_run()
    context.generate_baseline_values()
    context.cache.end_run()
    pc.display('done')
    if profile is not None:
        profile.disable()
        profile.dump_stats('baseline_profile.out')

if args.check or args.update_instance or args.update_nodes:
    if args.check:
        context.check_mode = True
        old_cache_prefix = context.cache.prefix
        context.cache.prefix = old_cache_prefix + '-' + str(time.time())
        for node_id, node in context.nodes.items():
            df = node.get_output()
            na_count = df.isna().sum().sum()
            if na_count:
                print('Node %s has NaN values:' % node.id)
                node.print_output()

            if node.baseline_values is not None:
                bdf = node.baseline_values
                na_count = bdf.null_count().sum(axis=1).sum()
                if na_count:
                    print('Node %s baseline forecast has NaN values:' % node.id)
                    node.print(node.baseline_values)
        context.cache.prefix = old_cache_prefix

    init_django()
    from nodes.models import InstanceConfig

    instance_obj = InstanceConfig.objects.filter(identifier=instance.id).first()
    if instance_obj is None:
        print("Creating instance %s" % instance.id)
        instance_obj = InstanceConfig.create_for_instance(instance)
        # TODO: Create InstanceHostname somewhere?

    if args.update_instance:
        instance_obj.update_from_instance(instance, overwrite=True)
        instance_obj.save()
    instance_obj.sync_nodes(update_existing=args.update_nodes, delete_stale=args.delete_stale_nodes)
    instance_obj.sync_dimensions()
    instance_obj.create_default_content()

for param_arg in (args.param or []):
    param_id, val = param_arg.split('=')
    context.set_parameter_value(param_id, val)

for node_id in (args.node or []):
    node = context.get_node(node_id)
    node.print_output()
    node.plot_output()
    if isinstance(node, ActionNode):
        output_nodes = node.output_nodes
        for n in output_nodes:
            print("Impact of %s on %s" % (node, n))
            node.print_impact(n)

        """
        for n in context.nodes.values():
            if n.output_nodes:
                # Not a leaf node
                continue
            if n not in downstream_nodes:
                print("%s has no impact on %s" % (node, n))
                continue
        """

if args.print_action_efficiencies:
    def print_action_efficiencies():
        context.cache.start_run()
        pc = PerfCounter("Action efficiencies")
        for aep in context.action_efficiency_pairs:
            title = '%s / %s' % (aep.cost_node.id, aep.impact_node.id)
            pc.display('%s starting' % title)
            table = Table(title=title)
            table.add_column("Action", "Cumulative efficiency")
            if args.node:
                actions = [context.get_action(node_id) for node_id in args.node]
            else:
                actions = None
            for out in aep.calculate_iter(context, actions=actions):
                action = out.action
                pc.display('%s computed' % action.id)
                table.add_row(action.id, str(out.cumulative_efficiency))
                # action.print_pint_df(out.df)

            console = Console()
            console.print(table)

        context.cache.end_run()

    if profile is not None:
        profile.enable()
    print_action_efficiencies()
    if profile is not None:
        profile.disable()
        profile.dump_stats('action_efficiencies_profile.out')


if args.show_perf:
    context.perf_context.print()


if False:
    loader.context.dataset_repo.pull_datasets()
    loader.context.print_all_parameters()
    loader.context.generate_baseline_values()
    # for sector in page.get_sectors():
    #    print(sector)

