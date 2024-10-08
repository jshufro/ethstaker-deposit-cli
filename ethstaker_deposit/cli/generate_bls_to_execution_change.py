import os
import click
import json
import concurrent.futures
from typing import (
    Any,
    Sequence,
    Dict,
    Optional
)

from eth_typing import HexAddress

from ethstaker_deposit.credentials import (
    CredentialList,
    Credential
)
from ethstaker_deposit.utils.ascii_art import OWL_0
from ethstaker_deposit.utils.validation import (
    validate_bls_withdrawal_credentials_list,
    validate_bls_withdrawal_credentials_matching,
    validate_withdrawal_address,
    validate_int_range,
    verify_bls_to_execution_change_json,
    validate_validator_indices,
)
from ethstaker_deposit.utils.constants import (
    DEFAULT_BLS_TO_EXECUTION_CHANGES_FOLDER_NAME,
    MAX_DEPOSIT_AMOUNT,
)
from ethstaker_deposit.utils.click import (
    captive_prompt_callback,
    choice_prompt_func,
    jit_option,
)
from ethstaker_deposit.exceptions import ValidationError
from ethstaker_deposit.utils.intl import (
    closest_match,
    load_text,
)
from ethstaker_deposit.settings import (
    MAINNET,
    ALL_CHAIN_KEYS,
    get_chain_setting,
    get_devnet_chain_setting,
)
from .existing_mnemonic import (
    load_mnemonic_arguments_decorator,
)


def get_password(text: str) -> str:
    return click.prompt(text, hide_input=True, show_default=False, type=str)


def _validate_credentials_match(kwargs: Dict[str, Any]) -> Optional[ValidationError]:
    credential: Credential = kwargs.pop('credential')
    bls_withdrawal_credentials: bytes = kwargs.pop('bls_withdrawal_credentials')

    try:
        validate_bls_withdrawal_credentials_matching(bls_withdrawal_credentials, credential)
    except ValidationError as e:
        return e
    return None


FUNC_NAME = 'generate_bls_to_execution_change'


@click.command(
    help=load_text(['arg_generate_bls_to_execution_change', 'help'], func=FUNC_NAME),
)
@jit_option(
    default=os.getcwd(),
    help=lambda: load_text(['arg_bls_to_execution_changes_folder', 'help'], func=FUNC_NAME),
    param_decls='--bls_to_execution_changes_folder',
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
)
@jit_option(
    callback=captive_prompt_callback(
        lambda x: closest_match(x, ALL_CHAIN_KEYS),
        choice_prompt_func(
            lambda: load_text(['arg_chain', 'prompt'], func=FUNC_NAME),
            ALL_CHAIN_KEYS
        ),
    ),
    default=MAINNET,
    help=lambda: load_text(['arg_chain', 'help'], func=FUNC_NAME),
    param_decls='--chain',
    prompt=choice_prompt_func(
        lambda: load_text(['arg_chain', 'prompt'], func=FUNC_NAME),
        ALL_CHAIN_KEYS
    ),
)
@load_mnemonic_arguments_decorator
@jit_option(
    callback=captive_prompt_callback(
        lambda num: validate_int_range(num, 0, 2**32),
        lambda: load_text(['arg_validator_start_index', 'prompt'], func=FUNC_NAME),
    ),
    default=0,
    help=lambda: load_text(['arg_validator_start_index', 'help'], func=FUNC_NAME),
    param_decls="--validator_start_index",
    prompt=lambda: load_text(['arg_validator_start_index', 'prompt'], func=FUNC_NAME),
)
@jit_option(
    callback=captive_prompt_callback(
        lambda validator_indices: validate_validator_indices(validator_indices),
        lambda: load_text(['arg_validator_indices', 'prompt'], func=FUNC_NAME),
    ),
    help=lambda: load_text(['arg_validator_indices', 'help'], func=FUNC_NAME),
    param_decls='--validator_indices',
    prompt=lambda: load_text(['arg_validator_indices', 'prompt'], func=FUNC_NAME),
)
@jit_option(
    callback=captive_prompt_callback(
        lambda bls_withdrawal_credentials_list:
            validate_bls_withdrawal_credentials_list(bls_withdrawal_credentials_list),
        lambda: load_text(['arg_bls_withdrawal_credentials_list', 'prompt'], func=FUNC_NAME),
        prompt_if_none=True,
    ),
    help=lambda: load_text(['arg_bls_withdrawal_credentials_list', 'help'], func=FUNC_NAME),
    param_decls='--bls_withdrawal_credentials_list',
    prompt=False,  # the callback handles the prompt, to avoid second callback with bytes
)
@jit_option(
    callback=captive_prompt_callback(
        lambda address: validate_withdrawal_address(None, None, address),
        lambda: load_text(['arg_withdrawal_address', 'prompt'], func=FUNC_NAME),
        lambda: load_text(['arg_withdrawal_address', 'confirm'], func=FUNC_NAME),
        lambda: load_text(['arg_withdrawal_address', 'mismatch'], func=FUNC_NAME),
        prompt_if_none=True,
    ),
    help=lambda: load_text(['arg_withdrawal_address', 'help'], func=FUNC_NAME),
    param_decls=['--withdrawal_address'],
    prompt=False,  # the callback handles the prompt
)
@jit_option(
    # Only for devnet tests
    default=None,
    help="[DEVNET ONLY] Set specific GENESIS_FORK_VERSION value",
    param_decls='--devnet_chain_setting',
)
@click.pass_context
def generate_bls_to_execution_change(
        ctx: click.Context,
        bls_to_execution_changes_folder: str,
        chain: str,
        mnemonic: str,
        mnemonic_password: str,
        validator_start_index: int,
        validator_indices: Sequence[int],
        bls_withdrawal_credentials_list: Sequence[bytes],
        withdrawal_address: HexAddress,
        devnet_chain_setting: str,
        **kwargs: Any) -> None:
    # Generate folder
    bls_to_execution_changes_folder = os.path.join(
        bls_to_execution_changes_folder,
        DEFAULT_BLS_TO_EXECUTION_CHANGES_FOLDER_NAME,
    )
    if not os.path.exists(bls_to_execution_changes_folder):
        os.mkdir(bls_to_execution_changes_folder)

    # Get chain setting
    chain_setting = get_chain_setting(chain)

    if devnet_chain_setting is not None:
        click.echo('\n%s\n' % '**[Warning] Using devnet chain setting to generate the SignedBLSToExecutionChange.**\t')
        devnet_chain_setting_dict = json.loads(devnet_chain_setting)
        chain_setting = get_devnet_chain_setting(
            network_name=devnet_chain_setting_dict['network_name'],
            genesis_fork_version=devnet_chain_setting_dict['genesis_fork_version'],
            exit_fork_version=devnet_chain_setting_dict['exit_fork_version'],
            genesis_validator_root=devnet_chain_setting_dict['genesis_validator_root'],
        )

    if len(validator_indices) != len(bls_withdrawal_credentials_list):
        raise ValueError(
            "The size of `validator_indices` (%d) should be as same as `bls_withdrawal_credentials_list` (%d)."
            % (len(validator_indices), len(bls_withdrawal_credentials_list))
        )

    num_validators = len(validator_indices)
    amounts = [MAX_DEPOSIT_AMOUNT] * num_validators

    credentials = CredentialList.from_mnemonic(
        mnemonic=mnemonic,
        mnemonic_password=mnemonic_password,
        num_keys=num_validators,
        amounts=amounts,
        chain_setting=chain_setting,
        start_index=validator_start_index,
        hex_withdrawal_address=withdrawal_address,
    )

    # Check if the given old bls_withdrawal_credentials is as same as the mnemonic generated
    with click.progressbar(length=len(credentials.credentials),  # type: ignore[var-annotated]
                           label=load_text(['msg_credentials_verification']),
                           show_percent=False, show_pos=True) as bar:
        executor_kwargs = [{
            'credential': credential,
            'bls_withdrawal_credentials': bls_withdrawal_credentials_list[i],
        } for i, credential in enumerate(credentials.credentials)]

        with concurrent.futures.ProcessPoolExecutor() as executor:
            for e in executor.map(_validate_credentials_match, executor_kwargs):
                bar.update(1)
                if e is not None:
                    click.echo('\n\n[Error] ' + str(e))
                    return

    btec_file = credentials.export_bls_to_execution_change_json(bls_to_execution_changes_folder, validator_indices)

    json_file_validation_result = verify_bls_to_execution_change_json(
        btec_file,
        credentials.credentials,
        input_validator_indices=validator_indices,
        input_withdrawal_address=withdrawal_address,
        chain_setting=chain_setting,
    )
    if not json_file_validation_result:
        raise ValidationError(load_text(['err_verify_btec']))

    click.clear()
    click.echo(OWL_0)
    click.echo(load_text(['msg_creation_success']) + str(bls_to_execution_changes_folder))

    click.pause(load_text(['msg_pause']))
